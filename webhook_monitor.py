#!/usr/bin/env python3
"""
GitHub Webhook Monitor - отслеживает изменения в master ветке
и запускает автоматическую сборку и развертывание
"""

import os
import json
import hmac
import hashlib
import subprocess
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import requests
from github import Github
from packaging.version import parse as parse_version
import tempfile
import shutil
import asyncio
import xml.etree.ElementTree as ET
from apscheduler.schedulers.background import BackgroundScheduler
from pathlib import Path

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webhook_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="GitHub CI/CD Monitor", version="1.0.0")
templates = Jinja2Templates(directory="/app/templates")


class RepoRegistry:
    """Хранит список отслеживаемых репозиториев и веток в repos.json"""

    def __init__(self, path: str = "repos.json"):
        self.path = path
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump([], f)

    def _load(self) -> list[dict]:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: list[dict]):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def list(self) -> list[dict]:
        return self._load()

    def add(self, repo_url: str, branch: str = "main") -> bool:
        repos = self._load()
        if any(r["repo_url"] == repo_url and r["branch"] == branch for r in repos):
            return False
        repos.append({"repo_url": repo_url, "branch": branch})
        self._save(repos)
        return True

    def remove(self, repo_url: str, branch: str) -> bool:
        repos = self._load()
        new_repos = [r for r in repos if not (r["repo_url"] == repo_url and r["branch"] == branch)]
        if len(new_repos) == len(repos):
            return False  # not found
        self._save(new_repos)
        return True


repo_registry = RepoRegistry()


class GitHubMonitor:
    """Минимальная реализация мониторинга для запуска сервера.
    Полноценная логика будет добавлена позже, сейчас нужны только методы,
    которые вызываются из роутов: verify_signature, process_build, send_notification.
    """

    def __init__(self):
        self.secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
        self.active_builds: dict[str, dict] = {}

    def verify_signature(self, payload: bytes, signature_header: str) -> bool:
        """Проверка подписи webhook. Если секрет не задан, всегда True."""
        if not self.secret:
            return True
        if not signature_header:
            return False
        sha_name, signature = signature_header.split("=", 1)
        if sha_name != "sha256":
            return False
        mac = hmac.new(self.secret.encode(), msg=payload, digestmod=hashlib.sha256)
        return hmac.compare_digest(mac.hexdigest(), signature)

    async def process_build(self, repo_data: dict):
        logger.info(f"Stub process_build called for {repo_data.get('name')}")
        # эмуляция длительной задачи
        await asyncio.sleep(0.1)

    async def send_notification(self, message: str, level: str = "info"):
        logger.info(f"Notification ({level}): {message}")


# Pydantic модели
class RegisterRequest(BaseModel):
    repo_url: str = Field(..., example="https://github.com/user/repo")
    branch: str = Field(default="main", example="main")

class UnregisterRequest(BaseModel):
    repo_url: str
    branch: str


class Dependency(BaseModel):
    name: str
    current_version: str
    latest_version: Optional[str] = None

class ScanResult(BaseModel):
    repo_url: str
    dependencies: list[Dependency]

class CreatePrRequest(BaseModel):
    repo_url: str
    packages: list[str] # Список имен пакетов для обновления

class UpdateResponse(BaseModel):
    pr_url: str

class SettingsRequest(BaseModel):
    github_token: str

# Конфигурация
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "your-secret-key")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
DOCKER_REGISTRY = os.getenv("DOCKER_REGISTRY", "localhost:5000")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

class DependencyManager:
    CONFIG_FILE = Path('config.json')
    # Неймспейс для pom.xml, необходим для корректного парсинга
    POM_XMLNS = {'m': 'http://maven.apache.org/POM/4.0.0'}

    def _get_token(self) -> Optional[str]:
        """Безопасно загружает токен из config.json."""
        if not self.CONFIG_FILE.exists():
            return None
        try:
            with open(self.CONFIG_FILE, 'r') as f:
                config = json.load(f)
            return config.get('GITHUB_TOKEN')
        except (json.JSONDecodeError, IOError):
            return None

    def _get_github_client(self) -> Optional[Github]:
        """Инициализирует клиент GitHub, используя токен из config.json."""
        token = self._get_token()
        if token:
            return Github(token)
        return None

    def __init__(self, github_token: str = None):
        # Инициализация с токеном из env для обратной совместимости или тестов,
        # но основной механизм - _get_github_client
        pass

    async def create_update_pr(self, repo_url: str, packages: list[str]) -> str:
        token = self._get_token()
        if not token:
            raise HTTPException(status_code=401, detail="GitHub token is not configured. Please set it in the settings.")
        
        github_client = Github(token)


        # Поскольку нам нужна полная информация о зависимостях (включая версии), 
        # мы повторно сканируем репозиторий.
        scan_result = await self.scan_repository(repo_url)

        if not scan_result.dependencies:
            raise Exception("No dependencies found or failed to parse dependency file.")

        # Фильтруем зависимости, оставляя только те, которые были выбраны для обновления.
        dependencies_to_update = [dep for dep in scan_result.dependencies if dep.name in packages]

        if not dependencies_to_update:
            raise Exception("No valid dependencies selected for update.")

        repo_name = '/'.join(repo_url.split('/')[-2:]).replace('.git', '')
        repo = github_client.get_repo(repo_name)

        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. Клонируем репозиторий с токеном для push
            auth_repo_url = repo.clone_url.replace('https://', f'https://oauth2:{token}@')
            clone_cmd = ["git", "clone", auth_repo_url, temp_dir]
            await self._run_command(clone_cmd)

            # 2. Создаем новую ветку
            branch_name = f"update-dependencies-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            await self._run_command(["git", "checkout", "-b", branch_name], cwd=temp_dir)

            # 3. Обновляем файл зависимостей
            # Определяем, какой файл обновлять
            pom_path = Path(temp_dir) / 'pom.xml'
            req_path = Path(temp_dir) / 'requirements.txt'

            if pom_path.exists():
                self._update_pom_file(pom_path, dependencies_to_update)
            elif req_path.exists():
                self._update_requirements_file(req_path, dependencies_to_update)
            else:
                logger.warning(f"No dependency file (pom.xml or requirements.txt) found in {repo_name}.")
                raise Exception("No dependency file found to update.")

            # 4. Коммитим изменения
            await self._run_command(["git", "add", str(pom_path if pom_path.exists() else req_path)], cwd=temp_dir)
            commit_message = "chore(deps): update project dependencies"
            await self._run_command(["git", "commit", "-m", commit_message], cwd=temp_dir)

            # 5. Отправляем ветку в GitHub
            await self._run_command(["git", "push", "origin", branch_name], cwd=temp_dir)

            # 6. Создаем Pull Request
            pr_title = "chore(deps): Update dependencies"
            pr_body = self._generate_pr_body(dependencies_to_update)
            pr = repo.create_pull(title=pr_title, body=pr_body, head=branch_name, base=repo.default_branch)

            return pr.html_url

    def _update_pom_file(self, file_path: Path, dependencies: list[Dependency]):
        """Обновляет версии в pom.xml"""
        try:
            ET.register_namespace('', self.POM_XMLNS['m'])
            tree = ET.parse(file_path)
            root = tree.getroot()
            
            for dep in dependencies:
                # Имя зависимости в pom.xml обычно groupId:artifactId
                groupId, artifactId = dep.name.split(':')
                # Ищем соответствующий элемент
                node = root.find(f".//m:dependency[m:groupId='{groupId}' and m:artifactId='{artifactId}']", self.POM_XMLNS)
                if node:
                    version_node = node.find('m:version', self.POM_XMLNS)
                    if version_node is not None:
                        logger.info(f"Updating {dep.name} to {dep.latest_version} in pom.xml")
                        version_node.text = dep.latest_version
            
            tree.write(file_path, encoding='utf-8', xml_declaration=True)

        except ET.ParseError as e:
            logger.error(f"Error parsing pom.xml: {e}")
            raise

    def _update_requirements_file(self, file_path: Path, dependencies: list[Dependency]):
        with open(file_path, 'r') as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            updated = False
            for dep in dependencies:
                if dep.name in line and dep.current_version != dep.latest_version:
                    new_lines.append(f"{dep.name}=={dep.latest_version}\n")
                    updated = True
                    break
            if not updated:
                new_lines.append(line)

        with open(file_path, 'w') as f:
            f.writelines(new_lines)

    
    async def build_docker_image(self, project_dir: str, jar_path: str, image_name: str) -> str:
        """Создание Docker образа"""
        try:
            # Создаем Dockerfile если его нет
            dockerfile_path = os.path.join(project_dir, "Dockerfile")
            if not os.path.exists(dockerfile_path):
                await self.create_dockerfile(dockerfile_path, jar_path)
            
            # Строим Docker образ
            image_tag = f"{DOCKER_REGISTRY}/{image_name}:latest"
            
            build_cmd = [
                "docker", "build",
                "-t", image_tag,
                "-f", dockerfile_path,
                project_dir
            ]
            
            result = subprocess.run(build_cmd, capture_output=True, text=True, timeout=900)
            
            if result.returncode != 0:
                raise Exception(f"Docker build failed: {result.stderr}")
            
            logger.info(f"Docker image built: {image_tag}")
            return image_tag
            
        except Exception as e:
            logger.error(f"Docker build failed: {e}")
            raise
    
    async def create_dockerfile(self, dockerfile_path: str, jar_path: str):
        """Создание Dockerfile для Java приложения"""
        jar_name = os.path.basename(jar_path)
        
        dockerfile_content = f"""FROM openjdk:17-jre-slim

WORKDIR /app

# Копируем JAR файл
COPY build/libs/{jar_name} app.jar

# Создаем пользователя для безопасности
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

# Настройки JVM
ENV JAVA_OPTS="-Xmx512m -Xms256m"

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \\
  CMD curl -f http://localhost:8080/actuator/health || exit 1

# Экспонируем порт
EXPOSE 8080

# Запускаем приложение
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
"""
        
        with open(dockerfile_path, 'w') as f:
            f.write(dockerfile_content)
        
        logger.info(f"Dockerfile created: {dockerfile_path}")
    
    async def deploy_container(self, image_tag: str, container_name: str):
        """Развертывание контейнера"""
        try:
            # Останавливаем старый контейнер
            stop_cmd = ["docker", "stop", container_name]
            subprocess.run(stop_cmd, capture_output=True)
            
            # Удаляем старый контейнер
            remove_cmd = ["docker", "rm", container_name]
            subprocess.run(remove_cmd, capture_output=True)
            
            # Запускаем новый контейнер
            run_cmd = [
                "docker", "run",
                "-d",
                "--name", container_name,
                "-p", "8080:8080",
                "--restart", "unless-stopped",
                image_tag
            ]
            
            result = subprocess.run(run_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                raise Exception(f"Container deployment failed: {result.stderr}")
            
            logger.info(f"Container deployed: {container_name}")
            
        except Exception as e:
            logger.error(f"Deployment failed: {e}")
            raise
    
    async def process_build(self, repo_data: Dict[str, Any]):
        """Обработка сборки проекта"""
        repo_url = repo_data['clone_url']
        repo_name = repo_data['name']
        commit_sha = repo_data.get('commit_sha', 'latest')
        
        build_id = f"{repo_name}_{commit_sha[:8]}"
        
        try:
            self.active_builds[build_id] = {
                'status': 'started',
                'start_time': datetime.now(),
                'repo_name': repo_name
            }
            
            await self.send_notification(
                f"🚀 Starting build for {repo_name} (commit: {commit_sha[:8]})",
                "info"
            )
            
            # Клонируем репозиторий
            project_dir = await self.clone_repository(repo_url)
            
            # Собираем проект
            jar_path = await self.build_gradle_project(project_dir)
            
            # Создаем Docker образ
            image_tag = await self.build_docker_image(project_dir, jar_path, repo_name)
            
            # Развертываем контейнер
            await self.deploy_container(image_tag, f"{repo_name}-app")
            
            # Обновляем статус
            self.active_builds[build_id]['status'] = 'success'
            self.active_builds[build_id]['end_time'] = datetime.now()
            
            await self.send_notification(
                f"✅ Build and deployment successful for {repo_name}",
                "success"
            )
            
            # Очищаем временные файлы
            subprocess.run(["rm", "-rf", project_dir], capture_output=True)
            
        except Exception as e:
            self.active_builds[build_id]['status'] = 'failed'
            self.active_builds[build_id]['error'] = str(e)
            self.active_builds[build_id]['end_time'] = datetime.now()
            
            await self.send_notification(
                f"❌ Build failed for {repo_name}: {str(e)}",
                "error"
            )
            
            logger.error(f"Build failed for {repo_name}: {e}")

# Инициализация монитора
# Scheduler
scheduler = BackgroundScheduler()

async def scheduled_scan():
    for repo in repo_registry.list():
        try:
            await dependency_manager.scan_repository(repo['repo_url'])
        except Exception as e:
            logger.error(f"Scheduled scan failed for {repo['repo_url']}: {e}")

scheduler.add_job(lambda: asyncio.run(scheduled_scan()), 'interval', hours=6)
scheduler.start()

monitor = GitHubMonitor()
dependency_manager = DependencyManager()

@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Обработчик GitHub webhook"""
    try:
        # Получаем данные
        payload = await request.body()
        signature = request.headers.get('X-Hub-Signature-256', '')
        event_type = request.headers.get('X-GitHub-Event', '')
        
        # Верифицируем подпись
        if not monitor.verify_signature(payload, signature):
            raise HTTPException(status_code=403, detail="Invalid signature")
        
        # Парсим JSON
        data = json.loads(payload.decode())
        
        # Обрабатываем только push события в master ветку
        if event_type == 'push' and data.get('ref') == 'refs/heads/master':
            repo_data = {
                'name': data['repository']['name'],
                'clone_url': data['repository']['clone_url'],
                'commit_sha': data['head_commit']['id']
            }

            # Добавляем в очередь сборки
            background_tasks.add_task(monitor.process_build, repo_data)

            return JSONResponse({
                "status": "accepted",
                "message": f"Build queued for {repo_data['name']}"
            })

        return JSONResponse({
            "status": "ignored",
            "message": f"Event {event_type} ignored"
        })

    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/builds")
async def get_builds():
    """Получение статуса сборок"""
    return JSONResponse(monitor.active_builds)

@app.get("/dependencies", response_class=HTMLResponse)
async def get_dependencies_page(request: Request):
    return templates.TemplateResponse("dependencies.html", {"request": request})

@app.get("/api/scan-dependencies", response_model=ScanResult)
async def api_scan_dependencies(repo_url: str):
    if not repo_url:
        raise HTTPException(status_code=400, detail="repo_url query parameter is required.")
    try:
        result = await dependency_manager.scan_repository(repo_url)
        return result
    except Exception as e:
        logger.error(f"Failed to scan repository {repo_url}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to scan repository: {e}")


@app.post("/api/settings")
async def save_settings(settings: SettingsRequest):
    try:
        config_data = {}
        if dependency_manager.CONFIG_FILE.exists():
            with open(dependency_manager.CONFIG_FILE, 'r') as f:
                try:
                    config_data = json.load(f)
                except json.JSONDecodeError:
                    pass # Файл пуст или поврежден, будет перезаписан
        
        config_data['GITHUB_TOKEN'] = settings.github_token
        
        with open(dependency_manager.CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
            
        return {"message": "Settings saved successfully."}
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {e}")

@app.post("/api/create-pr", response_model=UpdateResponse)
async def api_create_pr(pr_request: CreatePrRequest):
    try:
        pr_url = await dependency_manager.create_update_pr(pr_request.repo_url, pr_request.packages)
        return UpdateResponse(pr_url=pr_url)
    except Exception as e:
        logger.error(f"Failed to create PR for {pr_request.repo_url}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create PR: {e}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_builds": len(monitor.active_builds)
    })

@app.post("/api/register")
async def api_register_repo(req: RegisterRequest, request: Request):
    """Регистрирует репозиторий и создает GitHub webhook."""
    try:
        if not repo_registry.add(req.repo_url, req.branch):
            raise HTTPException(status_code=400, detail="Repository already registered.")

        token = dependency_manager._get_token()
        if not token:
            raise HTTPException(status_code=401, detail="GitHub token not configured.")

        github_client = Github(token)
        owner_repo = req.repo_url.replace("https://github.com/", "").rstrip("/")
        repo = github_client.get_repo(owner_repo)

        webhook_url = str(request.base_url).rstrip("/") + "/webhook"
        # Создаем webhook, если его ещё нет
        if not any(h.config.get('url') == webhook_url for h in repo.get_hooks()):
            repo.create_hook(
                "web",
                {
                    "url": webhook_url,
                    "content_type": "json",
                    "secret": GITHUB_WEBHOOK_SECRET,
                },
                events=["push"],
                active=True,
            )
        return {"message": "Repository registered."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Register repo error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/repos")
async def api_list_repos():
    """Список зарегистрированных репозиториев"""
    return repo_registry.list()


@app.post("/api/unregister")
async def api_unregister_repo(req: UnregisterRequest):
    """Удаляет репозиторий из реестра"""
    try:
        if not repo_registry.remove(req.repo_url, req.branch):
            raise HTTPException(status_code=404, detail="Repository not found.")
        return {"message": "Repository unregistered."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unregister repo error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
