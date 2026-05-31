import os
import re
import uuid
import time
import logging
import datetime
import threading
import subprocess
import urllib.parse
import psycopg2
from http.server import HTTPServer, BaseHTTPRequestHandler

# Настройка логирования
LOG_DIR = "/logs"
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "server.log")),
        logging.StreamHandler()
    ]
)

UPLOAD_DIR = "/images"
BACKUP_DIR = "/backups"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif'}

# Настройки подключения к БД
DB_HOST = os.getenv("DB_HOST", "db")
DB_NAME = os.getenv("DB_NAME", "images_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def init_db():
    """Создание таблицы при старте"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS images (
                id SERIAL PRIMARY KEY,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                size INTEGER NOT NULL,
                upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_type TEXT NOT NULL
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        logging.info("База данных успешно инициализирована.")
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")


def create_backup():
    """Функция создания бэкапа с датой и временем в названии"""
    try:
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_filename = f"backup_{now}.sql"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        cmd = f"PGPASSWORD={DB_PASSWORD} pg_dump -h {DB_HOST} -U {DB_USER} {DB_NAME} > {backup_path}"
        subprocess.run(cmd, shell=True, check=True)
        logging.info(f"Автоматический бэкап успешно создан: {backup_filename}")
    except Exception as e:
        logging.error(f"Ошибка при автоматическом создании бэкапа: {e}")


def backup_scheduler_loop():
    """Фоновый цикл резервного копирования (раз в 10 минут)"""
    time.sleep(10)
    INTERVAL = 600
    logging.info("Запущен фоновый планировщик резервного копирования.")
    while True:
        create_backup()
        time.sleep(INTERVAL)


class ImageServerHandler(BaseHTTPRequestHandler):

    def send_response_json(self, status_code, data):
        import json
        response_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def send_html(self, status_code, html_content):
        response_bytes = html_content.encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_url.query)
        clean_path = parsed_url.path.rstrip('/')

        # МАРШРУТ СТИРАЕТ СЛЭШИ И ПРОВЕРЯЕТ /list
        if clean_path == '/list':
            try:
                page = int(params.get('page', [1])[0])
                if page < 1: page = 1
            except ValueError:
                page = 1

            limit = 10
            offset = (page - 1) * limit

            try:
                conn = get_db_connection()
                cur = conn.cursor()

                # 1. Считаем общее количество картинок
                cur.execute("SELECT COUNT(*) FROM images;")
                total_images = cur.fetchone()[0]

                # 2. Получаем картинки для текущей страницы
                cur.execute(
                    "SELECT id, filename, original_name, size, upload_time FROM images ORDER BY upload_time DESC LIMIT %s OFFSET %s;",
                    (limit, offset)
                )
                rows = cur.fetchall()
                cur.close()
                conn.close()

                # 3. Строим HTML страницу
                html = f"""
                                <!DOCTYPE html>
                                <html>
                                <head>
                                    <title>Upload Photos</title>
                                    <meta charset="utf-8">
                                    <style>
                                        body {{
                                            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                                            margin: 0;
                                            padding: 40px 20px;
                                            background: #f5f6ff;
                                            color: #1a1a2e;
                                            display: flex;
                                            flex-direction: column;
                                            align-items: center;
                                        }}
                                        h1 {{
                                            font-size: 20px;
                                            margin-bottom: 5px;
                                            color: #000000;
                                            font-weight: 600;
                                        }}
                                        .subtitle {{
                                            font-size: 14px;
                                            color: #666;
                                            margin-bottom: 40px;
                                        }}
                                        /* Вкладки (Табы) */
                                        .tabs {{
                                            display: flex;
                                            gap: 25px;
                                            margin-bottom: 35px;
                                            font-size: 22px;
                                            font-weight: bold;
                                        }}
                                        .tab {{
                                            text-decoration: none;
                                            color: #cbd3f7;
                                            cursor: pointer;
                                            transition: color 0.2s;
                                        }}
                                        .tab.active-tab {{
                                            color: #4370ff;
                                        }}
                                        .tab:hover {{
                                            color: #4370ff;
                                        }}

                                        /* Контейнер для контента */
                                        .container {{
                                            width: 100%;
                                            max-width: 650px;
                                            display: none;
                                        }}
                                        .container.active-content {{
                                            display: block;
                                        }}

                                        /* Форма загрузки (Макет 777) */
                                        .drop-zone {{
                                            background: #fdfdff;
                                            border: 2px dashed #4370ff;
                                            border-radius: 12px;
                                            padding: 45px 20px;
                                            text-align: center;
                                            cursor: pointer;
                                            position: relative;
                                        }}
                                        .drop-zone icon {{
                                            display: block;
                                            font-size: 40px;
                                            color: #4370ff;
                                            margin-bottom: 15px;
                                        }}
                                        .drop-zone p {{
                                            font-size: 16px;
                                            margin: 10px 0;
                                            font-weight: 500;
                                        }}
                                        .drop-zone .info {{
                                            font-size: 13px;
                                            color: #b5b9d2;
                                        }}
                                        .file-input {{
                                            position: absolute;
                                            top: 0; left: 0; width: 100%; height: 100%;
                                            opacity: 0; cursor: pointer;
                                        }}
                                        .btn-browse {{
                                            background: #0052ff;
                                            color: white;
                                            border: none;
                                            width: 100%;
                                            padding: 14px;
                                            border-radius: 8px;
                                            font-size: 16px;
                                            font-weight: 500;
                                            cursor: pointer;
                                            margin-top: 20px;
                                            transition: background 0.2s;
                                        }}
                                        .btn-browse:hover {{ background: #003dd1; }}

                                        .current-upload {{
                                            margin-top: 35px;
                                            width: 100%;
                                        }}
                                        .current-upload label {{
                                            font-weight: bold;
                                            font-size: 14px;
                                            display: block;
                                            margin-bottom: 8px;
                                        }}
                                        .url-box {{
                                            display: flex;
                                            background: white;
                                            border: 1px solid #dcdfe6;
                                            border-radius: 8px;
                                            padding: 8px 12px;
                                            align-items: center;
                                        }}
                                        .url-box input {{
                                            border: none; width: 100%; outline: none; color: #606266; font-size: 14px;
                                        }}
                                        .btn-copy {{
                                            background: #0052ff; color: white; border: none; padding: 6px 14px;
                                            border-radius: 6px; font-size: 12px; font-weight: bold; cursor: pointer;
                                        }}

                                        /* Список файлов (Макет 888) */
                                        .file-list {{
                                            width: 100%;
                                        }}
                                        .list-header {{
                                            display: flex;
                                            justify-content: space-between;
                                            font-weight: 500;
                                            color: #1a1a2e;
                                            padding: 0 10px 15px 10px;
                                            font-size: 14px;
                                        }}
                                        .file-row {{
                                            display: flex;
                                            align-items: center;
                                            background: transparent;
                                            padding: 15px 10px;
                                            border-bottom: 1px solid #eef0f7;
                                        }}
                                        .file-icon {{
                                            color: #0052ff; font-size: 18px; margin-right: 15px; display: flex; align-items: center;
                                        }}
                                        .file-name {{
                                            flex: 1; font-size: 15px; color: #1a1a2e; font-weight: 500;
                                            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-right: 20px;
                                        }}
                                        .file-url {{
                                            flex: 2; font-size: 14px; color: #4370ff; text-decoration: none;
                                            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-right: 20px;
                                        }}
                                        .btn-delete {{
                                            background: #ffebeb; color: #ff4d4d; border: none; width: 32px; height: 32px;
                                            border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center;
                                            font-size: 14px; transition: background 0.2s;
                                        }}
                                        .btn-delete:hover {{ background: #ffdadb; }}

                                        .pagination {{
                                            margin-top: 25px; text-align: center; display: flex; justify-content: center; gap: 10px;
                                        }}
                                        .pagination a, .pagination span {{
                                            padding: 8px 16px; background: white; border: 1px solid #e0e0e0;
                                            text-decoration: none; color: #1a1a2e; border-radius: 6px; font-size: 14px;
                                        }}
                                        .pagination .disabled {{ color: #ccc; background: #f5f5f5; cursor: not-allowed; }}
                                    </style>
                                </head>
                                <body>

                                    <h1>Upload Photos</h1>
                                    <div class="subtitle">Upload selfies, memes, or any fun pictures here.</div>

                                    <div class="tabs">
                                        <a class="tab" id="tab-upload" onclick="switchTab('upload')">Upload</a>
                                        <a class="tab" id="tab-images" onclick="switchTab('images')">Images</a>
                                    </div>

                                    <div id="content-upload" class="container">
                                        <form id="upload-form" action="/upload" method="POST" enctype="multipart/form-data">
                                            <div class="drop-zone">
                                                <icon>☁️</icon>
                                                <p>Select a file or drag and drop here</p>
                                                <div class="info">Only support .jpg, .png and .gif. <br> Maximum file size is 5MB</div>
                                                <input type="file" name="image" class="file-input" onchange="submitForm()">
                                            </div>
                                            <button type="button" class="btn-browse" onclick="document.querySelector('.file-input').click()">Browse your file</button>
                                        </form>

                                        <div class="current-upload">
                                            <label>Current Upload</label>
                                            <div class="url-box">
                                                <input type="text" id="url-output" placeholder="https://" readonly>
                                                <button class="btn-copy" onclick="copyUrl()">COPY</button>
                                            </div>
                                        </div>
                                    </div>

                                    <div id="content-images" class="container">
                                        <div class="file-list">
                                            <div class="list-header">
                                                <span style="flex: 1;">Name</span>
                                                <span style="flex: 2; padding-left: 10px;">Url</span>
                                                <span style="width: 32px; text-align: right;">Delete</span>
                                            </div>
                                """

                # Рендерим строки файлов
                for row in rows:
                    img_id, filename, orig_name, size, uptime = row
                    full_url = f"http://localhost/images/{filename}"
                    html += f"""
                                            <div class="file-row">
                                                <div class="file-icon">🖼️</div>
                                                <div class="file-name" title="{orig_name}">{orig_name}</div>
                                                <a href="{full_url}" target="_blank" class="file-url" title="{full_url}">{full_url}</a>
                                                <button class="btn-delete" onclick="if(confirm('Удалить файл {orig_name}?')) location.href='/delete?id={img_id}&page={page}&view=images'">🗑️</button>
                                            </div>
                                    """
                # for row in rows:
                #     img_id, filename, orig_name, size, uptime = row
                #     full_url = f"http://localhost/images/{filename}"
                #     html += f"""
                #                             <div class="file-row">
                #                                 <div class="file-icon">🖼️</div>
                #                                 <div class="file-name" title="{orig_name}">{orig_name}</div>
                #                                 <a href="{full_url}" target="_blank" class="file-url" title="{full_url}">{full_url}</a>
                #                                 <button class="btn-delete" onclick="if(confirm('Удалить файл {orig_name}?')) location.href='/delete?id={img_id}'">🗑️</button>
                #                             </div>
                #                     """

                html += "</div>"  # Конец file-list

                # Пагинация
                html += "<div class='pagination'>"
                if page > 1:
                    html += f'<a href="/list?page={page - 1}&view=images">&laquo; Назад</a>'
                else:
                    html += '<span class="disabled">&laquo; Назад</span>'

                html += f'<span>Страница {page}</span>'

                if offset + limit < total_images:
                    html += f'<a href="/list?page={page + 1}&view=images">Вперед &raquo;</a>'
                else:
                    html += '<span class="disabled">Вперед &raquo;</span>'
                html += "</div>"  # Конец пагинации

                html += f"""
                                    </div>

                                    <script>
                                        // Переключение вкладок с автоматическим обновлением данных для Images
                                        function switchTab(tabName) {{
                                            const urlParams = new URLSearchParams(window.location.search);
                                            const currentView = urlParams.get('view') || 'upload';

                                            // Если пользователь нажал на Images, находясь на Upload — делаем мягкую перезагрузку страницы
                                            if (tabName === 'images' && currentView !== 'images') {{
                                                window.location.href = "/list?view=images";
                                                return;
                                            }}

                                            // Обычное визуальное переключение (для работы назад/вперед)
                                            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active-tab'));
                                            document.querySelectorAll('.container').forEach(c => c.classList.remove('active-content'));

                                            document.getElementById('tab-' + tabName).classList.add('active-tab');
                                            document.getElementById('content-' + tabName).classList.add('active-content');

                                            const url = new URL(window.location);
                                            url.searchParams.set('view', tabName);
                                            window.history.pushState({{}}, '', url);
                                        }}

                                        // Автоматическая отправка формы при выборе файла
                                        function submitForm() {{
                                            const form = document.getElementById('upload-form');
                                            const formData = new FormData(form);

                                            fetch('/upload', {{
                                                method: 'POST',
                                                body: formData
                                            }})
                                            .then(response => response.json())
                                            .then(data => {{
                                                if (data.url) {{
                                                    document.getElementById('url-output').value = window.location.origin + data.url;
                                                    alert('Файл успешно загружен!');
                                                }} else if (data.error) {{
                                                    alert('Ошибка: ' + data.error);
                                                }}
                                            }})
                                            .catch(error => {{
                                                console.error('Error:', error);
                                                alert('Произошла ошибка при загрузке');
                                            }});
                                        }}

                                        function copyUrl() {{
                                            const copyText = document.getElementById('url-output');
                                            if(!copyText.value) return;
                                            copyText.select();
                                            document.execCommand("copy");
                                            alert("Ссылка скопирована в буфер обмена!");
                                        }}

                                        // Определяем, какую вкладку открыть при старте
                                        const urlParams = new URLSearchParams(window.location.search);
                                        const initialView = urlParams.get('view') || 'upload';

                                        // Первичный рендер активных классов без триггера перезагрузки
                                        document.getElementById('tab-' + initialView).classList.add('active-tab');
                                        document.getElementById('content-' + initialView).classList.add('active-content');
                                    </script>
                                </body>
                                </html>
                                """
                return self.send_html(200, html)
                # html = f"""
                # <!DOCTYPE html>
                # <html>
                # <head>
                #     <title>Сервер картинок 2.0</title>
                #     <style>
                #         body {{ font-family: Arial, sans-serif; margin: 40px; background: #f4f4f9; }}
                #         .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 20px; }}
                #         .card {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); text-align: center; }}
                #         .card img {{ max-width: 100%; max-height: 150px; border-radius: 4px; }}
                #         .btn-del {{ background: #ff4d4d; color: white; border: none; padding: 5px 10px; border-radius: 4px; cursor: pointer; margin-top: 10px; }}
                #         .pagination {{ margin-top: 30px; text-align: center; }}
                #         .pagination a, .pagination span {{ padding: 10px 15px; margin: 5px; background: white; border: 1px solid #ccc; text-decoration: none; color: black; border-radius: 4px; }}
                #         .pagination .disabled {{ color: #ccc; background: #eee; }}
                #     </style>
                # </head>
                # <body>
                #     <h1>Загруженные изображения (Всего: {total_images})</h1>
                #     <div class="grid">
                # """
                # for row in rows:
                #     img_id, filename, orig_name, size, uptime = row
                #     html += f"""
                #         <div class="card">
                #             <a href="/images/{filename}" target="_blank">
                #                 <img src="/images/{filename}" alt="{orig_name}">
                #             </a>
                #             <p style="font-size:12px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">{orig_name}</p>
                #             <button class="btn-del" onclick="if(confirm('Удалить картинку?')) location.href='/delete?id={img_id}'">Удалить</button>
                #         </div>
                #     """
                #
                # html += "</div><div class='pagination'>"
                # if page > 1:
                #     html += f'<a href="/list?page={page - 1}">&laquo; Назад</a>'
                # else:
                #     html += '<span class="disabled">&laquo; Назад</span>'
                #
                # html += f'<span>Страница {page}</span>'
                #
                # if offset + limit < total_images:
                #     html += f'<a href="/list?page={page + 1}">Вперед &raquo;</a>'
                # else:
                #     html += '<span class="disabled">Вперед &raquo;</span>'
                #
                # html += "</div></body></html>"
                # return self.send_html(200, html)

            except Exception as e:
                logging.error(f"Ошибка при генерации списка: {e}")
                return self.send_html(500, "<h1>Внутренняя ошибка сервера</h1>")

        elif clean_path == '/delete':
            img_id = params.get('id', [None])[0]
            # Считываем страницу, с которой было совершено удаление (по умолчанию 1)
            back_page = params.get('page', ['1'])[0]

            if not img_id:
                return self.send_response_json(400, {"error": "Не передан id для удаления"})

            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("SELECT filename FROM images WHERE id = %s;", (img_id,))
                result = cur.fetchone()

                if not result:
                    cur.close()
                    conn.close()
                    return self.send_response_json(404, {"error": "Изображение не найдено в БД"})

                filename = result[0]
                cur.execute("DELETE FROM images WHERE id = %s;", (img_id,))
                conn.commit()
                cur.close()
                conn.close()

                file_path = os.path.join(UPLOAD_DIR, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logging.info(f"Файл {filename} успешно удален.")

                # РЕДИРЕКТ: Возвращаем пользователя строго на ту же страницу и вкладку Images!
                self.send_response(303)
                self.send_header('Location', f'/list?page={back_page}&view=images')
                self.end_headers()
            except Exception as e:
                logging.error(f"Ошибка при удалении: {e}")
                return self.send_response_json(500, {"error": "Ошибка при удалении"})
        # elif clean_path == '/delete':
        #     img_id = params.get('id', [None])[0]
        #     if not img_id:
        #         return self.send_response_json(400, {"error": "Не передан id для удаления"})
        #
        #     try:
        #         conn = get_db_connection()
        #         cur = conn.cursor()
        #         cur.execute("SELECT filename FROM images WHERE id = %s;", (img_id,))
        #         result = cur.fetchone()
        #
        #         if not result:
        #             cur.close()
        #             conn.close()
        #             return self.send_response_json(404, {"error": "Изображение не найдено в БД"})
        #
        #         filename = result[0]
        #         cur.execute("DELETE FROM images WHERE id = %s;", (img_id,))
        #         conn.commit()
        #         cur.close()
        #         conn.close()
        #
        #         file_path = os.path.join(UPLOAD_DIR, filename)
        #         if os.path.exists(file_path):
        #             os.remove(file_path)
        #             logging.info(f"Файл {filename} успешно удален.")
        #
        #         self.send_response(303)
        #         self.send_header('Location', '/list')
        #         self.end_headers()
        #     except Exception as e:
        #         logging.error(f"Ошибка при удалении: {e}")
        #         return self.send_response_json(500, {"error": "Ошибка при удалении"})
        else:
            return self.send_response_json(404, {"error": "Страница не найдена"})

    def do_POST(self):
        if self.path != '/upload':
            return self.send_response_json(404, {"error": "Страница не найдена"})

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > MAX_CONTENT_LENGTH:
            return self.send_response_json(400, {"error": "Файл слишком большой"})

        content_type = self.headers.get('Content-Type', '')
        if not content_type.startswith('multipart/form-data'):
            return self.send_response_json(400, {"error": "Ожидается multipart/form-data"})

        boundary_match = re.search(r'boundary=([^;]+)', content_type)
        if not boundary_match:
            return self.send_response_json(400, {"error": "Некорректный запрос"})

        boundary = boundary_match.group(1).encode('utf-8')
        raw_body = self.rfile.read(content_length)
        parts = raw_body.split(b'--' + boundary)

        file_bytes = None
        filename = None

        for part in parts:
            if b'Content-Disposition' in part and b'filename=' in part:
                header_part, body_part = part.split(b'\r\n\r\n', 1)
                filename_match = re.search(r'filename="([^"]+)"', header_part.decode('utf-8', errors='ignore'))
                if filename_match:
                    filename = filename_match.group(1)
                if body_part.endswith(b'\r\n'):
                    body_part = body_part[:-2]
                file_bytes = body_part
                break

        if not file_bytes or not filename:
            return self.send_response_json(400, {"error": "Файл не отправлен"})

        _, ext = os.path.splitext(filename.lower())
        if ext not in ALLOWED_EXTENSIONS:
            return self.send_response_json(400, {"error": f"Неподдерживаемый формат"})

        unique_filename = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        file_size = len(file_bytes)

        try:
            with open(file_path, 'wb') as f:
                f.write(file_bytes)

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO images (filename, original_name, size, file_type) VALUES (%s, %s, %s, %s);",
                (unique_filename, filename, file_size, ext.replace('.', ''))
            )
            conn.commit()
            cur.close()
            conn.close()

            logging.info(f"Файл {unique_filename} сохранен.")
            return self.send_response_json(201, {"url": f"/images/{unique_filename}"})
        except Exception as e:
            logging.error(f"Ошибка при сохранении: {str(e)}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return self.send_response_json(500, {"error": "Внутренняя ошибка сервера"})


def run(server_class=HTTPServer, handler_class=ImageServerHandler, port=8000):
    init_db()
    scheduler_thread = threading.Thread(target=backup_scheduler_loop, daemon=True)
    scheduler_thread.start()

    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f"Запуск бэкенд сервера ТЗ 2.0 на порту {port}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == '__main__':
    run()