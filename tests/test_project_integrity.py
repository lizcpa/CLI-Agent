import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


class TestDatabaseSQL:
    def test_init_sql_syntax(self):
        sql_path = Path(__file__).parent.parent / "database" / "init.sql"
        assert sql_path.exists(), "init.sql not found"
        content = sql_path.read_text(encoding="utf-8")
        assert "CREATE TABLE" in content
        assert "CREATE DATABASE" in content

    def test_key_tables_defined(self):
        sql_path = Path(__file__).parent.parent / "database" / "init.sql"
        content = sql_path.read_text(encoding="utf-8")
        required_tables = [
            "products",
            "crawl_plans",
            "generation_pipelines",
            "publish_log",
            "model_usage_log",
            "platform_config",
            "tenant_config",
            "api_keys",
        ]
        for table in required_tables:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in content, f"Missing table: {table}"

    def test_timestamp_fields(self):
        sql_path = Path(__file__).parent.parent / "database" / "init.sql"
        content = sql_path.read_text(encoding="utf-8")
        assert "created_at" in content
        assert "updated_at" in content


class TestProjectCompile:
    def test_all_python_files_compile(self):
        import py_compile
        root = Path(__file__).parent.parent
        errors = []
        for fp in root.rglob("*.py"):
            if ".venv" in str(fp) or ".git" in str(fp) or "node_modules" in str(fp):
                continue
            if "dist" in str(fp):
                continue
            try:
                py_compile.compile(str(fp), doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(f"{fp}: {e}")
        assert len(errors) == 0, f"Compilation errors:\n" + "\n".join(errors)

    def test_all_service_dirs_have_main(self):
        backend = Path(__file__).parent.parent / "project" / "backend"
        service_dirs = [d for d in backend.iterdir() if d.is_dir()]
        for sd in service_dirs:
            assert (sd / "main.py").exists(), f"Missing main.py in {sd.name}"

    def test_all_service_dirs_have_init(self):
        backend = Path(__file__).parent.parent / "project" / "backend"
        service_dirs = [d for d in backend.iterdir() if d.is_dir()]
        for sd in service_dirs:
            assert (sd / "__init__.py").exists(), f"Missing __init__.py in {sd.name}"


class TestDependencies:
    def test_requirements_exists(self):
        req_path = Path(__file__).parent.parent / "requirements.txt"
        assert req_path.exists()
        content = req_path.read_text()
        assert "fastapi" in content
        assert "pydantic" in content
        assert "celery" in content

    def test_docker_compose_exists(self):
        dc_path = Path(__file__).parent.parent / "docker-compose.yml"
        assert dc_path.exists()
        content = dc_path.read_text(encoding="utf-8")
        assert "mysql" in content
        assert "redis" in content
        assert "rabbitmq" in content
        assert "minio" in content
