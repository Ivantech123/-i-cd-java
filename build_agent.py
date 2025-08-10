#!/usr/bin/env python3
"""
Build Agent - изолированный сервис для сборки проектов
Работает с очередью Redis и выполняет сборки в изолированной среде
"""

import os
import json
import asyncio
import asyncio.subprocess
import logging
import subprocess
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

import redis
import asyncpg
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# База данных
Base = declarative_base()

class BuildRecord(Base):
    __tablename__ = 'builds'
    
    id = Column(Integer, primary_key=True)
    build_id = Column(String(100), unique=True, nullable=False)
    repo_name = Column(String(200), nullable=False)
    commit_sha = Column(String(40), nullable=False)
    status = Column(String(20), nullable=False)  # pending, building, success, failed
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    image_tag = Column(String(500), nullable=True)
    build_logs = Column(Text, nullable=True)

class BuildAgent:
    def __init__(self):
        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            decode_responses=True
        )
        
        # Настройка базы данных
        db_url = os.getenv('DATABASE_URL', 'postgresql://cicd_user:password@localhost:5432/cicd_monitor')
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        
        self.docker_registry = os.getenv('DOCKER_REGISTRY', 'localhost:5000')
        self.build_timeout = int(os.getenv('BUILD_TIMEOUT', 1800))  # 30 минут
        
    async def process_build_queue(self):
        """Основной цикл обработки очереди сборок"""
        logger.info("Build agent started, waiting for builds...")
        
        while True:
            try:
                # Получаем задачу из очереди
                build_data = self.redis_client.blpop('build_queue', timeout=10)
                
                if build_data:
                    _, build_json = build_data
                    build_task = json.loads(build_json)
                    
                    logger.info(f"Processing build: {build_task['build_id']}")
                    await self.execute_build(build_task)
                    
            except Exception as e:
                logger.error(f"Error processing build queue: {e}")
                await asyncio.sleep(5)
    
    async def execute_build(self, build_task: Dict[str, Any]):
        """Выполнение сборки проекта"""
        build_id = build_task['build_id']
        repo_url = build_task['repo_url']
        repo_name = build_task['repo_name']
        commit_sha = build_task['commit_sha']
        
        # Создаем запись в БД
        session = self.Session()
        build_record = BuildRecord(
            build_id=build_id,
            repo_name=repo_name,
            commit_sha=commit_sha,
            status='building',
            start_time=datetime.now()
        )
        session.add(build_record)
        session.commit()
        
        build_logs = []
        temp_dir = None
        
        try:
            # Создаем временную директорию
            temp_dir = tempfile.mkdtemp(prefix=f"build_{build_id}_")
            logger.info(f"Build directory: {temp_dir}")
            
            # Клонируем репозиторий
            await self.log_step(build_logs, "Cloning repository...")
            clone_dir = await self.clone_repository(repo_url, temp_dir, commit_sha)
            
            # Определяем тип проекта
            project_type = await self.detect_project_type(clone_dir)
            await self.log_step(build_logs, f"Detected project type: {project_type}")
            
            # Собираем проект
            await self.log_step(build_logs, "Building project...")
            artifact_path = await self.build_project(clone_dir, project_type)
            
            # Создаем Docker образ
            await self.log_step(build_logs, "Building Docker image...")
            image_tag = await self.build_docker_image(clone_dir, artifact_path, repo_name, project_type)
            
            # Публикуем образ в registry
            await self.log_step(build_logs, "Pushing to registry...")
            await self.push_to_registry(image_tag)
            
            # Обновляем статус
            build_record.status = 'success'
            build_record.end_time = datetime.now()
            build_record.image_tag = image_tag
            build_record.build_logs = '\n'.join(build_logs)
            
            # Уведомляем о успешной сборке
            await self.notify_build_complete(build_id, 'success', image_tag)
            
            logger.info(f"Build {build_id} completed successfully")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Build {build_id} failed: {error_msg}")
            
            build_record.status = 'failed'
            build_record.end_time = datetime.now()
            build_record.error_message = error_msg
            build_record.build_logs = '\n'.join(build_logs)
            
            await self.notify_build_complete(build_id, 'failed', error=error_msg)
            
        finally:
            session.commit()
            session.close()
            
            # Очищаем временные файлы
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
    
    async def log_step(self, build_logs: list, message: str):
        """Логирование шага сборки"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {message}"
        build_logs.append(log_entry)
        logger.info(log_entry)
    
    async def clone_repository(self, repo_url: str, temp_dir: str, commit_sha: str) -> str:
        """Клонирование репозитория"""
        clone_dir = os.path.join(temp_dir, 'source')
        
        # Клонируем репозиторий
        clone_cmd = ['git', 'clone', repo_url, clone_dir]
        result = await self.run_command(clone_cmd, timeout=300)
        
        if result.returncode != 0:
            raise Exception(f"Git clone failed: {result.stderr}")
        
        # Переключаемся на нужный коммит
        checkout_cmd = ['git', 'checkout', commit_sha]
        result = await self.run_command(checkout_cmd, cwd=clone_dir, timeout=60)
        
        if result.returncode != 0:
            raise Exception(f"Git checkout failed: {result.stderr}")
        
        return clone_dir
    
    async def detect_project_type(self, project_dir: str) -> str:
        """Определение типа проекта"""
        files = os.listdir(project_dir)
        
        if 'build.gradle' in files or 'build.gradle.kts' in files:
            return 'gradle'
        elif 'pom.xml' in files:
            return 'maven'
        elif 'package.json' in files:
            return 'nodejs'
        elif 'requirements.txt' in files or 'pyproject.toml' in files:
            return 'python'
        elif 'go.mod' in files:
            return 'golang'
        else:
            return 'unknown'
    
    async def build_project(self, project_dir: str, project_type: str) -> str:
        """Сборка проекта в зависимости от типа"""
        if project_type == 'gradle':
            return await self.build_gradle_project(project_dir)
        elif project_type == 'maven':
            return await self.build_maven_project(project_dir)
        elif project_type == 'nodejs':
            return await self.build_nodejs_project(project_dir)
        elif project_type == 'python':
            return await self.build_python_project(project_dir)
        elif project_type == 'golang':
            return await self.build_golang_project(project_dir)
        else:
            raise Exception(f"Unsupported project type: {project_type}")
    
    async def build_gradle_project(self, project_dir: str) -> str:
        """Сборка Gradle проекта"""
        # Проверяем наличие wrapper
        gradlew_path = os.path.join(project_dir, 'gradlew')
        if os.path.exists(gradlew_path):
            # Делаем gradlew исполняемым
            os.chmod(gradlew_path, 0o755)
            gradle_cmd = ['./gradlew']
        else:
            gradle_cmd = ['gradle']
        
        # Собираем проект
        build_cmd = gradle_cmd + ['clean', 'build', '-x', 'test']
        result = await self.run_command(build_cmd, cwd=project_dir, timeout=self.build_timeout)
        
        if result.returncode != 0:
            raise Exception(f"Gradle build failed: {result.stderr}")
        
        # Находим JAR файл
        libs_dir = os.path.join(project_dir, 'build', 'libs')
        if not os.path.exists(libs_dir):
            raise Exception("Build libs directory not found")
        
        jar_files = [f for f in os.listdir(libs_dir) if f.endswith('.jar') and 'sources' not in f]
        if not jar_files:
            raise Exception("No JAR file found after build")
        
        return os.path.join(libs_dir, jar_files[0])
    
    async def build_maven_project(self, project_dir: str) -> str:
        """Сборка Maven проекта"""
        build_cmd = ['mvn', 'clean', 'package', '-DskipTests']
        result = await self.run_command(build_cmd, cwd=project_dir, timeout=self.build_timeout)
        
        if result.returncode != 0:
            raise Exception(f"Maven build failed: {result.stderr}")
        
        # Находим JAR файл
        target_dir = os.path.join(project_dir, 'target')
        jar_files = [f for f in os.listdir(target_dir) if f.endswith('.jar') and 'sources' not in f]
        if not jar_files:
            raise Exception("No JAR file found after build")
        
        return os.path.join(target_dir, jar_files[0])
    
    async def build_nodejs_project(self, project_dir: str) -> str:
        """Сборка Node.js проекта"""
        # Устанавливаем зависимости
        install_cmd = ['npm', 'install']
        result = await self.run_command(install_cmd, cwd=project_dir, timeout=600)
        
        if result.returncode != 0:
            raise Exception(f"npm install failed: {result.stderr}")
        
        # Собираем проект
        build_cmd = ['npm', 'run', 'build']
        result = await self.run_command(build_cmd, cwd=project_dir, timeout=self.build_timeout)
        
        if result.returncode != 0:
            # Если нет build скрипта, возвращаем исходную директорию
            return project_dir
        
        # Проверяем наличие dist или build директории
        for build_dir in ['dist', 'build', 'public']:
            full_path = os.path.join(project_dir, build_dir)
            if os.path.exists(full_path):
                return full_path
        
        return project_dir
    
    async def build_python_project(self, project_dir: str) -> str:
        """Сборка Python проекта"""
        # Устанавливаем зависимости
        if os.path.exists(os.path.join(project_dir, 'requirements.txt')):
            install_cmd = ['pip', 'install', '-r', 'requirements.txt']
            result = await self.run_command(install_cmd, cwd=project_dir, timeout=600)
            
            if result.returncode != 0:
                raise Exception(f"pip install failed: {result.stderr}")
        
        return project_dir
    
    async def build_golang_project(self, project_dir: str) -> str:
        """Сборка Go проекта"""
        build_cmd = ['go', 'build', '-o', 'app', '.']
        result = await self.run_command(build_cmd, cwd=project_dir, timeout=self.build_timeout)
        
        if result.returncode != 0:
            raise Exception(f"Go build failed: {result.stderr}")
        
        return os.path.join(project_dir, 'app')
    
    async def build_docker_image(self, project_dir: str, artifact_path: str, repo_name: str, project_type: str) -> str:
        """Создание Docker образа"""
        # Создаем Dockerfile если его нет
        dockerfile_path = os.path.join(project_dir, 'Dockerfile')
        if not os.path.exists(dockerfile_path):
            await self.create_dockerfile(dockerfile_path, artifact_path, project_type)
        
        # Создаем тег образа
        image_tag = f"{self.docker_registry}/{repo_name}:latest"
        
        # Собираем образ
        build_cmd = ['docker', 'build', '-t', image_tag, project_dir]
        result = await self.run_command(build_cmd, timeout=900)
        
        if result.returncode != 0:
            raise Exception(f"Docker build failed: {result.stderr}")
        
        return image_tag
    
    async def create_dockerfile(self, dockerfile_path: str, artifact_path: str, project_type: str):
        """Создание Dockerfile в зависимости от типа проекта"""
        dockerfiles = {
            'gradle': self.create_java_dockerfile,
            'maven': self.create_java_dockerfile,
            'nodejs': self.create_nodejs_dockerfile,
            'python': self.create_python_dockerfile,
            'golang': self.create_golang_dockerfile
        }
        
        if project_type in dockerfiles:
            dockerfile_content = dockerfiles[project_type](artifact_path)
        else:
            raise Exception(f"No Dockerfile template for project type: {project_type}")
        
        with open(dockerfile_path, 'w') as f:
            f.write(dockerfile_content)
    
    def create_java_dockerfile(self, artifact_path: str) -> str:
        jar_name = os.path.basename(artifact_path)
        return f"""FROM openjdk:17-jre-slim

WORKDIR /app
COPY {jar_name} app.jar

RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
"""
    
    def create_nodejs_dockerfile(self, artifact_path: str) -> str:
        return """FROM node:18-alpine

WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production

COPY . .

RUN addgroup -g 1001 -S nodejs
RUN adduser -S nextjs -u 1001
RUN chown -R nextjs:nodejs /app
USER nextjs

EXPOSE 3000
CMD ["npm", "start"]
"""
    
    def create_python_dockerfile(self, artifact_path: str) -> str:
        return """FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["python", "app.py"]
"""
    
    def create_golang_dockerfile(self, artifact_path: str) -> str:
        return """FROM alpine:latest

RUN apk --no-cache add ca-certificates
WORKDIR /root/

COPY app .

RUN adduser -D -s /bin/sh appuser
USER appuser

EXPOSE 8080
CMD ["./app"]
"""
    
    async def push_to_registry(self, image_tag: str):
        """Публикация образа в registry"""
        push_cmd = ['docker', 'push', image_tag]
        result = await self.run_command(push_cmd, timeout=600)
        
        if result.returncode != 0:
            raise Exception(f"Docker push failed: {result.stderr}")
    
    async def notify_build_complete(self, build_id: str, status: str, image_tag: str = None, error: str = None):
        """Уведомление о завершении сборки"""
        notification = {
            'build_id': build_id,
            'status': status,
            'timestamp': datetime.now().isoformat(),
            'image_tag': image_tag,
            'error': error
        }
        
        # Отправляем уведомление в Redis
        self.redis_client.publish('build_notifications', json.dumps(notification))
    
    async def run_command(self, cmd: list, cwd: str = None, timeout: int = 300) -> subprocess.CompletedProcess:
        """Выполнение команды с таймаутом (асинхронно)"""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                raise Exception(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")
            return subprocess.CompletedProcess(cmd, process.returncode, stdout.decode(), stderr.decode())
        except Exception:
            raise

async def main():
    """Главная функция"""
    agent = BuildAgent()
    await agent.process_build_queue()

if __name__ == "__main__":
    asyncio.run(main())
