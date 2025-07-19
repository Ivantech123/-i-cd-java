-- Создание базы данных и пользователя
CREATE DATABASE cicd_monitor;
CREATE USER cicd_user WITH ENCRYPTED PASSWORD 'secure-password';
GRANT ALL PRIVILEGES ON DATABASE cicd_monitor TO cicd_user;

-- Подключение к базе данных
\c cicd_monitor;

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
