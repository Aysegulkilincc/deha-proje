from flask import Flask, request, jsonify
from flask_pymongo import PyMongo
from flask_cors import CORS
import bcrypt
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app) # Web sitesinden gelen isteklere izin ver

# MongoDB Bağlantısı (Senin mevcut .env dosyanı kullanır)
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
mongo = PyMongo(app)

# -----------------------------------
# 1. KAYIT OLMA (REGISTER)
# -----------------------------------
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    users = mongo.db.users
    
    # Kullanıcı var mı kontrol et
    if users.find_one({"email": data['email']}):
        return jsonify({"message": "Bu e-posta zaten kayıtlı!"}), 400
    
    # Şifreyi şifrele (Hash)
    hashed_password = bcrypt.hashpw(data['password'].encode('utf-8'), bcrypt.gensalt())
    
    users.insert_one({
        "name": data['name'],
        "email": data['email'],
        "password": hashed_password,
        "baby_name": data.get('baby_name', 'Bebeğim')
    })
    
    return jsonify({"message": "Başarıyla kayıt olundu!"}), 201

# -----------------------------------
# 2. GİRİŞ YAPMA (LOGIN)
# -----------------------------------
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    users = mongo.db.users
    user = users.find_one({"email": data['email']})
    
    if user and bcrypt.checkpw(data['password'].encode('utf-8'), user['password']):
        return jsonify({
            "message": "Giriş başarılı!",
            "user": {
                "name": user['name'],
                "baby_name": user['baby_name']
            }
        }), 200
    
    return jsonify({"message": "Hatalı e-posta veya şifre!"}), 401

if __name__ == '__main__':
    app.run(debug=True, port=5000)