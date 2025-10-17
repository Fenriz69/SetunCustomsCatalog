from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import gspread
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
import os
import json
import random
from collections import Counter
import threading
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

# --- НАСТРОЙКИ ---
SPREADSHEET_ID = '1wDcqbg0dkI_P7bZRzaU5J88mjbHNUHZpYwDpdImVbUk'
SHEET_NAME_CATALOG = 'Лист1'
SHEET_NAME_SHIPPED = 'Отгружено'
SHEET_NAME_SETTINGS = 'Настройки'

IMAGE_FOLDER = 'images'
DATA_START_ROW = 2
CACHE_FILE = 'catalog_cache.json'

# --- КАРТА СТОЛБЦОВ ---
COLUMN_MAP = {
    'sku': 0, 'stock': 1, 'name': 3, 'archive_name': 4, 'race': 5, 'class': 6,
}

# --- ЛОГИКА ПРЕФИКСОВ ---
POSSIBLE_PREFIXES = ["Enemy - ", "Hero - ", "Weapon - ", "Bust - ", "NPC - ", "Special - ", "Xmas Special -"]
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

script_dir = os.path.dirname(os.path.abspath(__file__))
abs_image_folder_path = os.path.join(script_dir, IMAGE_FOLDER)
cache_file_path = os.path.join(script_dir, CACHE_FILE)

def get_creds():
    creds_json_str = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json_str:
        raise ValueError("Переменная окружения GOOGLE_CREDENTIALS не найдена.")
    creds_info = json.loads(creds_json_str)
    return service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)

def get_local_images():
    if not os.path.isdir(abs_image_folder_path):
        return {}
    
    image_map = {}
    for root, dirs, files in os.walk(abs_image_folder_path):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                folder_name = os.path.basename(root)
                relative_path = os.path.join(folder_name, file)
                url_path = relative_path.replace('\\', '/')
                lowercase_folder_name = folder_name.strip().lower()
                if lowercase_folder_name not in image_map:
                    image_map[lowercase_folder_name] = url_path
    return image_map

def find_image_locally(archive_name, image_map):
    search_name = archive_name.lower()
    path_segment = None

    if search_name in image_map:
        path_segment = image_map[search_name]
    else:
        for prefix in POSSIBLE_PREFIXES:
            prefixed_name = f"{prefix.lower()}{search_name}"
            if prefixed_name in image_map:
                path_segment = image_map[prefixed_name]
                break
    
    if path_segment:
        return f'/images/{path_segment}'
    return None

def update_cache():
    """Основная функция для обновления кэша из Google Sheets."""
    print("Начало обновления кэша...")
    try:
        creds = get_creds()
        sheets_service = gspread.authorize(creds)
        image_map = get_local_images()

        catalog_sheet = sheets_service.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME_CATALOG)
        shipped_sheet = sheets_service.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME_SHIPPED)
        settings_sheet = sheets_service.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME_SETTINGS)

        all_catalog_values = catalog_sheet.get_all_values()
        all_shipped_values = shipped_sheet.get_all_values()
        all_settings_values = settings_sheet.get_all_values()

        shipped_skus = [row[COLUMN_MAP['sku']].strip() for row in all_shipped_values[1:] if row and len(row) > COLUMN_MAP['sku']]
        sales_counts = Counter(shipped_skus)
        
        manual_top_skus = []
        for row in all_settings_values:
            if row and row[0].strip().lower() == 'manual_top_sales' and len(row) > 1:
                manual_top_skus = [sku.strip() for sku in row[1].split(',') if sku.strip()]
                break

        data_rows = all_catalog_values[DATA_START_ROW - 1:]
        formatted_data = []
        for row in data_rows:
            name = row[COLUMN_MAP['name']].strip() if len(row) > COLUMN_MAP['name'] else ''
            if not name:
                continue

            sku = row[COLUMN_MAP['sku']].strip() if len(row) > COLUMN_MAP['sku'] else 'N/A'
            archive_name = row[COLUMN_MAP['archive_name']].strip() if len(row) > COLUMN_MAP['archive_name'] else ''
            image_url = find_image_locally(archive_name, image_map)
            
            stock_val_str = row[COLUMN_MAP['stock']].strip() if len(row) > COLUMN_MAP['stock'] else '0'
            try:
                stock = int(stock_val_str)
            except (ValueError, TypeError):
                stock = 0
            
            formatted_item = {
                "sku": sku, "name": name, "stock": stock,
                "race": row[COLUMN_MAP['race']].strip() if len(row) > COLUMN_MAP['race'] else 'Не указана',
                "class": row[COLUMN_MAP['class']].strip() if len(row) > COLUMN_MAP['class'] else 'Не указан',
                "imageUrl": image_url or 'https://placehold.co/400x400/e2e8f0/64748b?text=Нет+Фото',
                "sales": sales_counts.get(sku, 0)
            }
            formatted_data.append(formatted_item)

        top_sales_items = []
        if manual_top_skus:
            top_sales_map = {item['sku']: item for item in formatted_data}
            for sku in manual_top_skus:
                if sku in top_sales_map:
                    top_sales_items.append(top_sales_map[sku])
        else:
            top_sales_items = sorted([item for item in formatted_data if item['sales'] > 0], key=lambda x: x['sales'], reverse=True)[:20]

        random.shuffle(formatted_data)
        
        cache_data = {
            "catalog": formatted_data,
            "top_sales": top_sales_items
        }

        with open(cache_file_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=4)
        
        print("Обновление кэша успешно завершено.")

    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА при обновлении кэша: {e}")

@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(abs_image_folder_path, filename)

@app.route('/api/catalog', methods=['GET'])
def get_catalog_data():
    if not os.path.exists(cache_file_path):
        print("Кэш не найден. Запускаю первоначальное обновление...")
        update_cache()

    try:
        with open(cache_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        print(f"Ошибка чтения кэша: {e}")
        return jsonify({"error": "Ошибка сервера при чтении кэша."}), 500

if __name__ != '__main__':
    # Этот блок выполняется, когда Gunicorn запускает приложение.
    # Запускаем обновление кэша при старте
    startup_thread = threading.Thread(target=update_cache)
    startup_thread.start()

    # Настраиваем ежедневное обновление
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(update_cache, 'cron', hour=18, minute=0)
    scheduler.start()
    
    # Убеждаемся, что планировщик корректно завершает работу
    atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    # Этот блок для локального тестирования.
    print("Сервер запущен в режиме локальной отладки.")
    # Обновляем кэш при старте
    update_cache()
    # Запускаем веб-сервер
    app.run(host='0.0.0.0', port=5000)

