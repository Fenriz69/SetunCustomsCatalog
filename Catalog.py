from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials
import os
import json
from collections import Counter
import random

# --- НАСТРОЙКИ ---
SPREADSHEET_ID = '1wDcqbg0dkI_P7bZRzaU5J88mjbHNUHZpYwDpdImVbUk'
SHEET_NAME_CATALOG = 'Лист1'
SHEET_NAME_SHIPPED = 'Отгружено'
SHEET_NAME_SETTINGS = 'Настройки'
DATA_START_ROW = 2
IMAGE_FOLDER = 'images'

# Карта столбцов для каталога
COLUMN_MAP_CATALOG = {
    'sku': 0, 'stock': 1, 'name': 3, 'archive_name': 4, 'race': 5, 'class': 6,
}
# Карта столбцов для отгрузок (нужен только SKU)
COLUMN_MAP_SHIPPED = {'sku': 1}

POSSIBLE_PREFIXES = ["Enemy - ", "Hero - ", "Weapon - ", "Bust - ", "NPC - ", "Special - ", "Xmas Special -"]
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

# Инициализация Flask для обслуживания статических файлов из папки 'static'
app = Flask(__name__, static_folder='static', static_url_path='')

CORS(app)

# Надежное определение пути к папке с изображениями
script_dir = os.path.dirname(os.path.abspath(__file__))
abs_image_folder_path = os.path.join(script_dir, IMAGE_FOLDER)

def get_google_creds():
    """Безопасно считывает ключи из переменной окружения."""
    creds_json_str = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json_str:
        raise ValueError("Переменная окружения GOOGLE_CREDENTIALS не найдена.")
    creds_info = json.loads(creds_json_str)
    return Credentials.from_service_account_info(creds_info, scopes=SCOPES)

def get_local_images():
    if not os.path.isdir(abs_image_folder_path):
        return {}
    
    image_map = {}
    for root, dirs, files in os.walk(abs_image_folder_path):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                folder_name = os.path.basename(root)
                relative_path = os.path.join(folder_name, file).replace('\\', '/')
                lowercase_folder_name = folder_name.strip().lower()
                if lowercase_folder_name not in image_map:
                    image_map[lowercase_folder_name] = relative_path
                    
    print(f"Найдено {len(image_map)} изображений в подпапках '{IMAGE_FOLDER}'.")
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

@app.route('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(abs_image_folder_path, filename)

@app.route('/')
def serve_index():
    # Эта функция будет отдавать главную страницу
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/catalog', methods=['GET'])
def get_catalog_data():
    try:
        creds = get_google_creds()
        sheets_service = gspread.authorize(creds)
        spreadsheet = sheets_service.open_by_key(SPREADSHEET_ID)

        # 1. Получаем продажи
        shipped_sheet = spreadsheet.worksheet(SHEET_NAME_SHIPPED)
        shipped_values = shipped_sheet.get_all_values()
        sku_col_index = COLUMN_MAP_SHIPPED['sku']
        sold_skus = [row[sku_col_index].strip() for row in shipped_values[1:] if len(row) > sku_col_index and row[sku_col_index].strip()]
        sales_counts = Counter(sold_skus)

        # 2. Получаем каталог
        image_map = get_local_images()
        catalog_sheet = spreadsheet.worksheet(SHEET_NAME_CATALOG)
        all_values = catalog_sheet.get_all_values()
        data_rows = all_values[DATA_START_ROW - 1:]

        formatted_data = []
        for row in data_rows:
            name = row[COLUMN_MAP_CATALOG['name']].strip() if len(row) > COLUMN_MAP_CATALOG['name'] else ''
            if not name: continue

            sku = row[COLUMN_MAP_CATALOG['sku']].strip() if len(row) > COLUMN_MAP_CATALOG['sku'] else 'N/A'
            archive_name = row[COLUMN_MAP_CATALOG['archive_name']].strip() if len(row) > COLUMN_MAP_CATALOG['archive_name'] else ''
            
            try:
                stock = int(row[COLUMN_MAP_CATALOG['stock']].strip() if len(row) > COLUMN_MAP_CATALOG['stock'] else '0')
            except (ValueError, TypeError):
                stock = 0
            
            formatted_item = {
                "sku": sku, "name": name, "stock": stock,
                "race": row[COLUMN_MAP_CATALOG['race']].strip() if len(row) > COLUMN_MAP_CATALOG['race'] else 'Не указана',
                "class": row[COLUMN_MAP_CATALOG['class']].strip() if len(row) > COLUMN_MAP_CATALOG['class'] else 'Не указан',
                "imageUrl": find_image_locally(archive_name, image_map) or 'https://placehold.co/400x400/e2e8f0/64748b?text=Нет+Фото',
                "sales": sales_counts.get(sku, 0)
            }
            formatted_data.append(formatted_item)

        # 3. Получаем настройки
        try:
            settings_sheet = spreadsheet.worksheet(SHEET_NAME_SETTINGS)
            settings_values = settings_sheet.get_all_values()
            top_sales_skus = [row[0].strip() for row in settings_values[1:] if row and row[0].strip()]
        except gspread.exceptions.WorksheetNotFound:
            top_sales_skus = []
            print(f"Лист '{SHEET_NAME_SETTINGS}' не найден. Топ продаж будет определен автоматически.")

        return jsonify({"catalog": formatted_data, "topSalesSkus": top_sales_skus})

    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"Произошла внутренняя ошибка: {e}") 
        return jsonify({"error": f"Произошла внутренняя ошибка сервера: {type(e).__name__}"}), 500

# Эта часть нужна для Gunicorn, чтобы он мог найти приложение
if __name__ == '__main__':
    # Эта часть для локального запуска, gunicorn ее не использует
    print("Сервер запущен в режиме разработки. Откройте в браузере http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000)

