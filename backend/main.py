import os
import re
import uuid
import time
import logging
import datetime
import math
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
                total_pages = math.ceil(total_images / limit) if total_images > 0 else 1

                # 2. Получаем картинки для текущей страницы
                cur.execute(
                    "SELECT id, filename, original_name, size, upload_time FROM images ORDER BY upload_time DESC LIMIT %s OFFSET %s;",
                    (limit, offset)
                )
                rows = cur.fetchall()
                cur.close()
                conn.close()

                # 3. Динамически собираем данные для шаблона
                table_rows_html = ""
                for row in rows:
                    img_id, filename, orig_name, size, uptime = row
                    full_url = f"http://localhost/images/{filename}"

                    file_type = filename.split('.')[-1] if '.' in filename else 'unknown'
                    size_kb = round(size / 1024, 1) if size else 0
                    date_str = uptime.strftime("%Y-%m-%d %H:%M:%S") if hasattr(uptime, 'strftime') else str(uptime)

                    table_rows_html += f"""
                                    <tr>
                                        <td><a href="{full_url}" target="_blank" class="file-link">{filename}</a></td>
                                        <td>{orig_name}</td>
                                        <td>{size_kb}</td>
                                        <td>{date_str}</td>
                                        <td><strong>{file_type}</strong></td>
                                        <td>
                                            <button class="btn-delete" onclick="if(confirm('Удалить файл {orig_name}?')) location.href='/delete?id={img_id}&page={page}&view=images'">🗑️</button>
                                        </td>
                                    </tr>
                                    """

                    # Формируем пагинацию
                    pagination_html = "<div class='pagination'>"
                    if page > 1:
                        pagination_html += f'<a href="/list?page={page - 1}&view=images">&laquo; Назад</a>'
                    else:
                        pagination_html += '<span class="disabled">&laquo; Назад</span>'

                    # Формируем надпись на "Страница X из Y"
                    pagination_html += f'<span>Страница {page} из {total_pages}</span>'

                    if offset + limit < total_images:
                        pagination_html += f'<a href="/list?page={page + 1}&view=images">Вперед &raquo;</a>'
                    else:
                        pagination_html += '<span class="disabled">Вперед &raquo;</span>'
                    pagination_html += "</div>"

                # Читаем шаблон из отдельного файла и подставляем данные
                current_dir = os.path.dirname(os.path.abspath(__file__))
                template_path = os.path.join(current_dir, 'list_template.html')

                # Дополнительная проверка на случай запуска из корня контейнера
                if not os.path.exists(template_path):
                    template_path = '/app/list_template.html'  # Стандартный путь в Docker

                try:
                    with open(template_path, 'r', encoding='utf-8') as f:
                        html = f.read()

                    # Делаем замену плейсхолдеров на готовый контент
                    html = html.replace('{{TABLE_ROWS}}', table_rows_html)
                    html = html.replace('{{PAGINATION}}', pagination_html)

                    return self.send_html(200, html)
                except Exception as e:
                    logging.error(
                        f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось прочитать шаблон по пути {template_path}. Ошибка: {e}")
                    return self.send_response_json(500, {
                        "error": f"Ошибка загрузки интерфейса. Файл не найден по пути: {template_path}"})

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
        else:
            return self.send_response_json(404, {"error": "Страница не найдена"})

    def do_POST(self):
        if self.path != '/upload':
            return self.send_response_json(404, {"error": "Страница не найдена"})

        content_length = int(self.headers.get('Content-Length', 0))
        # 1. Изменили сообщение при превышении размера (по ТЗ)
        if content_length > MAX_CONTENT_LENGTH:
            return self.send_response_json(400, {"error": "Ошибка! Размер файла больше 5 МБ"})

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

        # Дополнительная проверка реального размера после парсинга
        if len(file_bytes) > MAX_CONTENT_LENGTH:
            return self.send_response_json(400, {"error": "Ошибка! Размер файла больше 5 МБ"})

        _, ext = os.path.splitext(filename.lower())
        # 2. Изменили сообщение при неверном формате (по ТЗ)
        if ext not in ALLOWED_EXTENSIONS:
            return self.send_response_json(400, {"error": "Ошибка! Тип файла не соответствует файлу картинок"})

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
            # 3. Возвращаем статус 200, чтобы фронтенд успешно зашел в ветку (status === 200)
            return self.send_response_json(200, {"url": f"/images/{unique_filename}"})
        except Exception as e:
            logging.error(f"Ошибка при сохранении: {str(e)}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return self.send_response_json(500, {"error": "Внутренняя ошибка сервера"})


    # def do_POST(self):
    #     if self.path != '/upload':
    #         return self.send_response_json(404, {"error": "Страница не найдена"})
    #
    #     content_length = int(self.headers.get('Content-Length', 0))
    #     if content_length > MAX_CONTENT_LENGTH:
    #         return self.send_response_json(400, {"error": "Файл слишком большой"})
    #
    #     content_type = self.headers.get('Content-Type', '')
    #     if not content_type.startswith('multipart/form-data'):
    #         return self.send_response_json(400, {"error": "Ожидается multipart/form-data"})
    #
    #     boundary_match = re.search(r'boundary=([^;]+)', content_type)
    #     if not boundary_match:
    #         return self.send_response_json(400, {"error": "Некорректный запрос"})
    #
    #     boundary = boundary_match.group(1).encode('utf-8')
    #     raw_body = self.rfile.read(content_length)
    #     parts = raw_body.split(b'--' + boundary)
    #
    #     file_bytes = None
    #     filename = None
    #
    #     for part in parts:
    #         if b'Content-Disposition' in part and b'filename=' in part:
    #             header_part, body_part = part.split(b'\r\n\r\n', 1)
    #             filename_match = re.search(r'filename="([^"]+)"', header_part.decode('utf-8', errors='ignore'))
    #             if filename_match:
    #                 filename = filename_match.group(1)
    #             if body_part.endswith(b'\r\n'):
    #                 body_part = body_part[:-2]
    #             file_bytes = body_part
    #             break
    #
    #     if not file_bytes or not filename:
    #         return self.send_response_json(400, {"error": "Файл не отправлен"})
    #
    #     _, ext = os.path.splitext(filename.lower())
    #     if ext not in ALLOWED_EXTENSIONS:
    #         return self.send_response_json(400, {"error": f"Неподдерживаемый формат"})
    #
    #     unique_filename = f"{uuid.uuid4().hex}{ext}"
    #     file_path = os.path.join(UPLOAD_DIR, unique_filename)
    #     file_size = len(file_bytes)
    #
    #     try:
    #         with open(file_path, 'wb') as f:
    #             f.write(file_bytes)
    #
    #         conn = get_db_connection()
    #         cur = conn.cursor()
    #         cur.execute(
    #             "INSERT INTO images (filename, original_name, size, file_type) VALUES (%s, %s, %s, %s);",
    #             (unique_filename, filename, file_size, ext.replace('.', ''))
    #         )
    #         conn.commit()
    #         cur.close()
    #         conn.close()
    #
    #         logging.info(f"Файл {unique_filename} сохранен.")
    #         return self.send_response_json(201, {"url": f"/images/{unique_filename}"})
    #     except Exception as e:
    #         logging.error(f"Ошибка при сохранении: {str(e)}")
    #         if os.path.exists(file_path):
    #             os.remove(file_path)
    #         return self.send_response_json(500, {"error": "Внутренняя ошибка сервера"})


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