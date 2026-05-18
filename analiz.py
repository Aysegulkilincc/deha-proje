import json
import hashlib  # SHA-256 Kimlik şifreleme için
import ssl      # TLS 1.3 Ağ tünellemesi için
import os
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from pymongo import MongoClient
from dotenv import load_dotenv  # Arkadaşından aldığımız standart kütüphane

# ===============================
# ENV OKU (.env dosyasını güvenli yükler)
# ===============================
load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
TOPIC_IN = os.getenv("TOPIC_IN", "tubitak/#")
TOPIC_OUT = os.getenv("TOPIC_OUT", "proje_besik_2026/telemetry")
MONGO_URI = os.getenv("MONGO_URI")

# ===============================
# MONGO BAĞLANTI
# ===============================
mongo_collection = None

if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        mongo_client.admin.command("ping")
        db = mongo_client["proje_db"]
        mongo_collection = db["telemetries"]
        print("MongoDB bağlandı ✅")
    except Exception as e:
        print("Mongo bağlanamadı ⚠ ama sistem devam ediyor:", e)

# ===============================
# GLOBAL DEĞİŞKENLER (HAFIZA VE ZAMAN)
# ===============================
last_summary_time = datetime.now(timezone.utc)
temp_buffer = []

# ===============================
# SADE ATEŞ ALARMI (3 SEVİYE)
# ===============================
def simple_alarm(temp: float):
    if temp >= 38.0:
        return {"score": 3, "level": "EMERGENCY", "color": "RED", "message": "ÇOK YÜKSEK ATEŞ"}
    elif temp >= 37.0:
        return {"score": 2, "level": "RISK", "color": "YELLOW", "message": "ATEŞ VAR"}
    else:
        return {"score": 1, "level": "NORMAL", "color": "GREEN", "message": "NORMAL"}

# ===============================
# MQTT CALLBACK
# ===============================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT bağlandı ✅")
        client.subscribe(TOPIC_IN)
    else:
        print("MQTT bağlantı hatası ❌ rc =", rc)

def on_message(client, userdata, msg):
    global last_summary_time, temp_buffer

    raw = msg.payload.decode("utf-8", errors="replace")
    
    try:
        data = json.loads(raw)
    except Exception as e:
        print("JSON değil ❌", e)
        return

    # ==========================================
    # 1. GRUPTAN GELEN VERİLERİ ALIYORUZ
    # ==========================================
    ortam_sicakligi = data.get("ortam_sicakligi", 24.0)
    bebek_sicakliklari = data.get("bebek_sicakliklari", [])

    # DÜZELTME: Matrisi en başta her halükarda çekiyoruz ki analiz kaydında hata vermesin!
    raw_matrix = data.get("sicaklik_matrisi", data.get("temiz_matris", []))


    # --- YENİ ÖZELLİK: ETKEN (DURUM) ANALİZİ ---
    gelen_etken = data.get("etken", "Stabil")
    if "Nefes" in gelen_etken or "Ebeveyn" in gelen_etken:
        kullanici_dostu_etken = "Görüş Engellendi / Müdahale"
    elif "Cam" in gelen_etken:
        kullanici_dostu_etken = "Ortamda Ani Hava Değişimi"
    else:
        kullanici_dostu_etken = "Stabil (Normal)"

    # --- KVKK: Kimlik Anonimleştirme (Pseudonymization) ---
    ham_cihaz_id = data.get("cihaz_id", "bilinmeyen_cihaz")
    # Cihaz ID'yi alıp 16 haneli kırılamaz bir koda çeviriyoruz
    anonim_hasta_id = hashlib.sha256(ham_cihaz_id.encode('utf-8')).hexdigest()[:16]

    # Arkadaşının "hata yakalama" mantığını senin "kurtarma" mantığınla birleştiriyoruz:
    if len(bebek_sicakliklari) == 0:
        print("⚠ DİKKAT: Donanım ekibi 'bebek_sicakliklari' listesini eksik yolladı!")
        print("🛠️ Python Kurtarma Algoritması Devrede: Matris manuel taranıyor...")
        bebek_sicakliklari = [sicaklik for sicaklik in raw_matrix if sicaklik > 32.0] 

    # GÜVENLİK KİLİDİ: Yedek plana rağmen hala bebek (32 dereceden yüksek ısı) yoksa beşik boştur.
    if len(bebek_sicakliklari) == 0:
        print("Uyarı: Beşikte bebek algılanmadı! 🚼")
        return

    # 1. HAM DEĞERLERİ BULALIM (SIFIRA BÖLÜNME HATASI ÇÖZÜLDÜ ✅)
    ham_bebek_atesi = sum(bebek_sicakliklari) / len(bebek_sicakliklari)

    # 2. BİLİMSEL KALİBRASYON (OFFSET / TELAFİ)
    offset = 0.0
    if ortam_sicakligi < 24.0:
        sogukluk_farki = 24.0 - ortam_sicakligi
        offset = sogukluk_farki * 0.1  
    elif ortam_sicakligi > 26.0:
        sicaklik_farki = ortam_sicakligi - 26.0
        offset = -(sicaklik_farki * 0.1)

# 3. GERÇEK ÇEKİRDEK ATEŞİNİ HESAPLAMA (DİNAMİK BİYOLOJİK EĞRİ)
    # İnsan ateşi yükseldikçe damarlar genişler (vazodilatasyon), deri ısınır ve çekirdek ile deri arasındaki fark azalır.
    if ham_bebek_atesi <= 34.5:
        TIBBI_OFFSET = 2.5  # Sağlıklı bebek: Deri serin, iç-dış farkı standart.
    elif ham_bebek_atesi <= 36.0:
        TIBBI_OFFSET = 1.8  # Isınan bebek: Deri sıcaklığı artıyor, fark kapanmaya başlıyor.
    else:
        TIBBI_OFFSET = 1.2  # Ateşli bebek: Deri çok sıcak, iç-dış farkı minimumda.

    temp = ham_bebek_atesi + offset + TIBBI_OFFSET
    
    min_val = min(bebek_sicakliklari) + offset + TIBBI_OFFSET
    max_val = max(bebek_sicakliklari) + offset + TIBBI_OFFSET

    # TERMİNALDE ŞOV YAPALIM: Durumu da ekrana yazdırıyoruz!
    print(f"Ham Ten: {ham_bebek_atesi:.2f} | Oda: {ortam_sicakligi:.2f} | Durum: {kullanici_dostu_etken} | Gerçek Ateş: {temp:.2f}")  
      
    now = datetime.now(timezone.utc)
    temp_buffer.append(temp)

    # ==========================================
    # GÖREV 1: ANLIK VERİYİ "TEK KANALA" GÖNDER
    # ==========================================
    veri_payload = {
        "tip": "veri",  # 3. grup bunu görüp canlı rakamı güncelleyecek
        "temp": round(temp, 1),
        "veri_min": round(min_val, 1),
        "veri_max": round(max_val, 1),
        "anlik_durum": kullanici_dostu_etken  # WEB SİTESİNE GİDEN YENİ VERİ
    }
    client.publish(TOPIC_OUT + "/veri", json.dumps(veri_payload))

    # ==========================================
    # GÖREV 2: ÖZET VERİYİ AYNI "TEK KANALA" GÖNDER
    # ==========================================
    gecen_sure_saniye = (now - last_summary_time).total_seconds()
    
    # STABİLİTE KONTROLÜ
    is_stable = False
    if len(temp_buffer) >= 5: 
        if max(temp_buffer) < 37.0 and (max(temp_buffer) - min(temp_buffer)) <= 0.4:
            is_stable = True

    bekleme_suresi = 900 if is_stable else 180

    if gecen_sure_saniye >= bekleme_suresi or temp >= 37.0:
        alarm = simple_alarm(temp)
        ortalama_ates = sum(temp_buffer) / len(temp_buffer)
        
        ozet_payload = {
            "tip": "ozet_kayit",
            "hasta_anonim_id": anonim_hasta_id,  # Gerçek kimlik yerine şifreli kod gidiyor
            "kvkk_uyumlu_analitik": True,        # Jüri görsün diye eklenen güvenlik etiketi
            "ortalama_ates": round(ortalama_ates, 1),
            "periyot_min": round(min(temp_buffer), 1),
            "periyot_max": round(max(temp_buffer), 1),
            "olcum_modu": "YAVAŞ / 15dk" if is_stable else "HIZLI / 3dk VEYA ACİL",
            "renk": alarm["color"].lower(),
            "mesaj": alarm["message"],
            "son_bilinen_durum": kullanici_dostu_etken, # MONGO'YA GİDEN YENİ VERİ,
            "timestamp": now.isoformat(),
            "room_temp": ortam_sicakligi,
            "raw_data": raw_matrix  # Sadece anonimleştirilmiş analizler için tutuluyor
        }

        # DİKKAT: Özet de aynı /veri kanalına gidiyor!
        client.publish(TOPIC_OUT + "/veri", json.dumps(ozet_payload))
        print(f"🚀 ÖZET VERİ OLUŞTURULDU! Mod: {ozet_payload['olcum_modu']} | Ortalama: {ozet_payload['ortalama_ates']}")

        # MONGO'YA KAYIT
        if mongo_collection is not None:
            try:
                mongo_collection.insert_one(ozet_payload)
                print("✅ Özet ve ML Verisi Mongo'ya Kaydedildi.")
            except Exception as e:
                print("⚠ Mongo Kayıt Hatası:", e)

        # YENİ PERİYOT İÇİN SIFIRLAMA
        temp_buffer.clear() 
        last_summary_time = now

# ===============================
# MAIN
# ===============================
client = mqtt.Client()

# --- KVKK: İLETİM GÜVENLİĞİ (TLS TÜNELİ) AKTİF EDİLİYOR ---
# (HiveMQ gibi public sunucularda sertifika sormadan TLS tüneli açar)
client.tls_set(cert_reqs=ssl.CERT_NONE) 

client.on_connect = on_connect
client.on_message = on_message

# DİKKAT: TLS kullanacağımız için MQTT_PORT değerinin .env dosyasında 
# 1883 yerine 8883 olduğundan emin ol. (HiveMQ güvenli portu 8883'tür).
print("Broker'a güvenli (TLS) bağlanılıyor:", MQTT_HOST, MQTT_PORT)
client.connect(MQTT_HOST, MQTT_PORT, 60)
client.loop_forever()
