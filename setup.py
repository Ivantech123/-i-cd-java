#!/usr/bin/env python3
"""
Setup script для автоматической настройки GitHub CI/CD Monitor
"""

import os
import sys
import subprocess
import secrets
import string
from pathlib import Path

def generate_secret_key(length=32):
    """Генерация случайного ключа"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def create_env_file():
    """Создание .env файла с настройками"""
    env_content = f"""# GitHub Configuration
GITHUB_WEBHOOK_SECRET={generate_secret_key()}
GITHUB_TOKEN=ghp_your-github-token-here

# Docker Registry
DOCKER_REGISTRY=localhost:5000

# Database
POSTGRES_PASSWORD={generate_secret_key(16)}
DATABASE_URL=postgresql://cicd_user:{generate_secret_key(16)}@postgres:5432/cicd_monitor

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK

# Build Configuration
BUILD_TIMEOUT=1800
MAX_CONCURRENT_BUILDS=3

# Security
JWT_SECRET_KEY={generate_secret_key(64)}
ENCRYPTION_KEY={generate_secret_key(32)}

# Monitoring
PROMETHEUS_ENABLED=true
GRAFANA_ADMIN_PASSWORD={generate_secret_key(12)}

# Logging
LOG_LEVEL=INFO
LOG_FILE=/app/logs/cicd-monitor.log
"""
    
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print("✅ .env файл создан с автоматически сгенерированными секретами")

def create_directories():
    """Создание необходимых директорий"""
    directories = [
        'logs',
        'ssl',
        'grafana/dashboards',
        'grafana/provisioning/dashboards',
        'grafana/provisioning/datasources',
        'nginx',
        'prometheus'
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    
    print("✅ Директории созданы")

def create_nginx_config():
    """Создание конфигурации Nginx"""
    nginx_config = """events {
    worker_connections 1024;
}

http {
    upstream webhook_monitor {
        server webhook-monitor:8000;
    }
    
    upstream grafana {
        server grafana:3000;
    }
    
    server {
        listen 80;
        server_name localhost;
        
        location / {
            proxy_pass http://webhook_monitor;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
        
        location /grafana/ {
            proxy_pass http://grafana/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
"""
    
    with open('nginx/nginx.conf', 'w') as f:
        f.write(nginx_config)
    
    print("✅ Nginx конфигурация создана")

def create_prometheus_config():
    """Создание конфигурации Prometheus"""
    prometheus_config = """global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'webhook-monitor'
    static_configs:
      - targets: ['webhook-monitor:8000']
    metrics_path: '/metrics'
    scrape_interval: 30s
    
  - job_name: 'docker'
    static_configs:
      - targets: ['docker-registry:5000']
    metrics_path: '/metrics'
    scrape_interval: 60s
"""
    
    with open('prometheus/prometheus.yml', 'w') as f:
        f.write(prometheus_config)
    
    print("✅ Prometheus конфигурация создана")

def create_grafana_datasource():
    """Создание datasource для Grafana"""
    datasource_config = """apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true
"""
    
    with open('grafana/provisioning/datasources/prometheus.yml', 'w') as f:
        f.write(datasource_config)
    
    print("✅ Grafana datasource создан")

def create_database_init():
    """Создание SQL скрипта инициализации БД"""
    init_sql = """-- Создание базы данных и пользователя
CREATE DATABASE cicd_monitor;
CREATE USER cicd_user WITH ENCRYPTED PASSWORD 'secure-password';
GRANT ALL PRIVILEGES ON DATABASE cicd_monitor TO cicd_user;

-- Подключение к базе данных
\\c cicd_monitor;

-- Создание таблиц
CREATE TABLE IF NOT EXISTS builds (
    id SERIAL PRIMARY KEY,
    build_id VARCHAR(100) UNIQUE NOT NULL,
    repo_name VARCHAR(200) NOT NULL,
    commit_sha VARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    error_message TEXT,
    image_tag VARCHAR(500),
    build_logs TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deployments (
    id SERIAL PRIMARY KEY,
    build_id VARCHAR(100) REFERENCES builds(build_id),
    environment VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    container_id VARCHAR(100),
    service_url VARCHAR(500)
);

-- Создание индексов
CREATE INDEX idx_builds_status ON builds(status);
CREATE INDEX idx_builds_repo_name ON builds(repo_name);
CREATE INDEX idx_builds_start_time ON builds(start_time);
CREATE INDEX idx_deployments_environment ON deployments(environment);

-- Предоставление прав пользователю
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO cicd_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO cicd_user;
"""
    
    with open('init.sql', 'w') as f:
        f.write(init_sql)
    
    print("✅ SQL скрипт инициализации создан")

def install_dependencies():
    """Установка Python зависимостей"""
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'], 
                      check=True, capture_output=True)
        print("✅ Python зависимости установлены")
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка установки зависимостей: {e}")
        return False
    return True

def check_docker():
    """Проверка наличия Docker"""
    try:
        subprocess.run(['docker', '--version'], check=True, capture_output=True)
        print("✅ Docker найден")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ Docker не найден. Установите Docker для продолжения.")
        return False

def main():
    """Главная функция настройки"""
    print("🚀 Настройка GitHub CI/CD Monitor...")
    print("=" * 50)
    
    # Проверяем Docker
    if not check_docker():
        sys.exit(1)
    
    # Создаем структуру проекта
    create_directories()
    create_env_file()
    create_nginx_config()
    create_prometheus_config()
    create_grafana_datasource()
    create_database_init()
    
    # Устанавливаем зависимости
    if not install_dependencies():
        print("⚠️  Продолжаем без установки Python зависимостей")
    
    print("\n" + "=" * 50)
    print("✅ Настройка завершена!")
    print("\nСледующие шаги:")
    print("1. Отредактируйте .env файл (добавьте GitHub токен)")
    print("2. Запустите: docker-compose up -d")
    print("3. Настройте webhook в GitHub репозитории:")
    print("   - URL: http://your-server/webhook")
    print("   - Content type: application/json")
    print("   - Secret: значение из GITHUB_WEBHOOK_SECRET в .env")
    print("   - Events: Push events")
    print("\n🌐 Сервисы будут доступны по адресам:")
    print("   - Webhook Monitor: http://localhost")
    print("   - Grafana: http://localhost:3000")
    print("   - Prometheus: http://localhost:9090")
    print("   - Docker Registry: http://localhost:5000")

if __name__ == "__main__":
    main()
