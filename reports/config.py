import os
from dotenv import load_dotenv

load_dotenv()

SSH_CONFIG = {
    'host': os.getenv('SSH_HOST', '10.177.140.34'),
    'username': os.getenv('SSH_USERNAME', 'admin'),
    'password': os.getenv('SSH_PASSWORD'),
}

if not SSH_CONFIG['password']:
    raise ValueError("SSH_PASSWORD не найден в .env файле или переменных окружения!")