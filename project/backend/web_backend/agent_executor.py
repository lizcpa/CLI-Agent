"""Agent orchestrator: manage CLI-based agent tools (Claude Code, Trae, etc.)."""

from __future__ import annotations

import asyncio
import hashlib
import base64
import json
import os
import shlex
import shutil
import time
import uuid
from datetime import datetime
from typing import Any

from utils.db_clients.mysql import get_mysql_client
from utils.db_clients.redis import get_redis_client
from utils.common_sdk.logger import get_logger

logger = get_logger(__name__)

# ---- Encryption helpers (simple Fernet based on JWT secret) ----

def _get_cipher():
    from cryptography.fernet import Fernet
    from .config import JWT_SECRET
    key = hashlib.sha256(JWT_SECRET.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key)
    return Fernet(fernet_key)

def encrypt_key(plaintext: str) -> str:
    try:
        return _get_cipher().encrypt(plaintext.encode()).decode()
    except Exception:
        return base64.b64encode(plaintext.encode()).decode()

def decrypt_key(ciphertext: str) -> str:
    try:
        return _get_cipher().decrypt(ciphertext.encode()).decode()
    except Exception:
        try:
            return base64.b64decode(ciphertext.encode()).decode()
        except Exception:
            return ""

# ---- Database initialization ----

INIT_SQL = [
    """CREATE TABLE IF NOT EXISTS agent_tools (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        cli_command VARCHAR(512) NOT NULL,
        description TEXT,
        enabled TINYINT(1) DEFAULT 1,
        sort_order INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS model_keys (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        model_id VARCHAR(128) NOT NULL UNIQUE,
        model_name VARCHAR(128) NOT NULL,
        provider VARCHAR(64) NOT NULL,
        api_key_encrypted TEXT NOT NULL,
        base_url VARCHAR(512) DEFAULT '',
        env_var_name VARCHAR(128) NOT NULL DEFAULT 'API_KEY',
        enabled TINYINT(1) DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS agent_tasks (
        id VARCHAR(64) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
        agent_tool_id VARCHAR(64) NOT NULL,
        agent_tool_name VARCHAR(128) DEFAULT '',
        model_id VARCHAR(128) NOT NULL,
        model_name VARCHAR(128) DEFAULT '',
        task_instruction TEXT NOT NULL,
        status ENUM('pending','running','completed','failed','cancelled') DEFAULT 'pending',
        output LONGTEXT,
        error_message TEXT,
        pid INT DEFAULT NULL,
        exit_code INT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP NULL,
        INDEX idx_tenant (tenant_id),
        INDEX idx_status (status),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS agent_scheduled_tasks (
        id VARCHAR(64) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
        agent_tool_id VARCHAR(64) NOT NULL,
        agent_tool_name VARCHAR(128) DEFAULT '',
        model_id VARCHAR(128) NOT NULL,
        model_name VARCHAR(128) DEFAULT '',
        task_instruction TEXT NOT NULL,
        interval_seconds INT NOT NULL DEFAULT 3600,
        enabled TINYINT(1) DEFAULT 1,
        last_run_at TIMESTAMP NULL,
        next_run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_task_id VARCHAR(64) DEFAULT '',
        run_count INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_enabled (enabled),
        INDEX idx_next_run (next_run_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]

DEFAULT_TOOLS = [
    ("claude_code", "Claude Code", 'claude -p {task} --model {model}', "Anthropic 出品的 AI 编程 Agent，支持自主编码、调试、执行任务", 1),
    ("aider", "Aider", 'aider --message {task} --model {model}', "开源 AI pair programming 工具", 2),
    ("trae", "Trae", 'trae {task} --model {model}', "字节出品的 AI IDE Agent", 3),
    ("custom", "自定义命令", '{task}', "用户自定义命令模板", 99),
]

DEFAULT_MODELS = [
    ("claude-sonnet-4-20250514", "Claude Sonnet 4", "anthropic", "ANTHROPIC_API_KEY", ""),
    ("claude-opus-4-1-20250805", "Claude Opus 4.1", "anthropic", "ANTHROPIC_API_KEY", ""),
    ("gpt-4o", "GPT-4o", "openai", "OPENAI_API_KEY", ""),
    ("gpt-4o-mini", "GPT-4o mini", "openai", "OPENAI_API_KEY", ""),
    ("veo-3", "Veo 3 (视频生成)", "google", "GOOGLE_API_KEY", ""),
    ("gemini-2.5-pro", "Gemini 2.5 Pro", "google", "GOOGLE_API_KEY", ""),
    ("doubao-pro-32k", "豆包 Pro 32k", "volcengine", "VOLCENGINE_API_KEY", "https://ark.cn-beijing.volces.com/api/v3"),
    ("doubao-1.5-pro-256k", "豆包 1.5 Pro 256k", "volcengine", "VOLCENGINE_API_KEY", "https://ark.cn-beijing.volces.com/api/v3"),
    ("deepseek-chat", "DeepSeek Chat", "deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com"),
    ("deepseek-reasoner", "DeepSeek R1", "deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com"),
    ("qwen-max", "通义千问 Max", "alibaba", "DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("sdxl", "Stable Diffusion XL", "stability", "STABILITY_API_KEY", ""),
]

_running_tasks: dict[str, asyncio.subprocess.Process] = {}


async def ensure_tables():
    mysql = get_mysql_client()
    for sql in INIT_SQL:
        await mysql.execute(sql)

    row = await mysql.fetchone("SELECT COUNT(*) AS c FROM agent_tools")
    if row and row.get("c", 0) == 0:
        for tid, name, cmd, desc, order in DEFAULT_TOOLS:
            await mysql.execute(
                "INSERT INTO agent_tools (id, name, cli_command, description, enabled, sort_order) "
                "VALUES (%s, %s, %s, %s, 1, %s)",
                (tid, name, cmd, desc, order),
            )
        logger.info("agent_tools seeded", count=len(DEFAULT_TOOLS))

    row = await mysql.fetchone("SELECT COUNT(*) AS c FROM model_keys")
    if row and row.get("c", 0) == 0:
        for mid, mname, provider, env_var, base_url in DEFAULT_MODELS:
            await mysql.execute(
                "INSERT INTO model_keys (model_id, model_name, provider, api_key_encrypted, base_url, env_var_name, enabled) "
                "VALUES (%s, %s, %s, %s, %s, %s, 0)",
                (mid, mname, provider, "", base_url, env_var),
            )
        logger.info("model_keys seeded", count=len(DEFAULT_MODELS))


# ---- CRUD helpers ----

def check_tool_available(cli_command: str) -> tuple[bool, str]:
    """Check if the CLI command's executable is resolvable on PATH.
    Returns (available, resolved_path_or_reason).
    """
    try:
        parts = shlex.split(cli_command)
    except ValueError:
        return False, "命令模板格式无效"
    if not parts:
        return False, "命令为空"
    exe = parts[0]
    resolved = shutil.which(exe)
    if resolved:
        return True, resolved
    return False, f"未在 PATH 中找到 '{exe}'，请先安装该工具"


async def list_agent_tools() -> list[dict]:
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT id, name, cli_command, description, enabled, sort_order, created_at "
        "FROM agent_tools ORDER BY sort_order, name"
    )
    items = []
    for r in (rows or []):
        available, resolved = check_tool_available(r["cli_command"])
        items.append({
            "id": r["id"],
            "name": r["name"],
            "cli_command": r["cli_command"],
            "description": r.get("description") or "",
            "enabled": bool(r.get("enabled")),
            "sort_order": r.get("sort_order") or 0,
            "available": available,
            "resolved_path": resolved if available else "",
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
        })
    return items


async def list_models() -> list[dict]:
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT model_id, model_name, provider, base_url, env_var_name, enabled, "
        "LENGTH(api_key_encrypted) AS key_len, created_at, updated_at "
        "FROM model_keys ORDER BY provider, model_name"
    )
    items = []
    for r in (rows or []):
        items.append({
            "model_id": r["model_id"],
            "model_name": r["model_name"],
            "provider": r["provider"],
            "base_url": r.get("base_url") or "",
            "env_var_name": r["env_var_name"],
            "has_key": (r.get("key_len") or 0) > 10,
            "enabled": bool(r.get("enabled")),
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
        })
    return items


async def save_model_key(model_id: str, api_key: str, base_url: str = "") -> bool:
    mysql = get_mysql_client()
    enc = encrypt_key(api_key)
    existing = await mysql.fetchone("SELECT model_id FROM model_keys WHERE model_id=%s", (model_id,))
    if existing:
        await mysql.execute(
            "UPDATE model_keys SET api_key_encrypted=%s, base_url=%s, enabled=1, updated_at=NOW() WHERE model_id=%s",
            (enc, base_url or "", model_id),
        )
    else:
        await mysql.execute(
            "INSERT INTO model_keys (model_id, model_name, provider, api_key_encrypted, base_url, env_var_name, enabled) "
            "VALUES (%s, %s, %s, %s, %s, %s, 1)",
            (model_id, model_id, "custom", enc, base_url, "API_KEY"),
        )
    return True


async def get_model_key(model_id: str) -> str:
    mysql = get_mysql_client()
    row = await mysql.fetchone(
        "SELECT api_key_encrypted FROM model_keys WHERE model_id=%s AND enabled=1",
        (model_id,),
    )
    if not row or not row.get("api_key_encrypted"):
        return ""
    return decrypt_key(row["api_key_encrypted"])


async def get_model_info(model_id: str) -> dict | None:
    mysql = get_mysql_client()
    row = await mysql.fetchone(
        "SELECT model_id, model_name, provider, base_url, env_var_name "
        "FROM model_keys WHERE model_id=%s",
        (model_id,),
    )
    return dict(row) if row else None


async def add_agent_tool(tool_id: str, name: str, cli_command: str, description: str = "") -> dict:
    mysql = get_mysql_client()
    await mysql.execute(
        "INSERT INTO agent_tools (id, name, cli_command, description, enabled, sort_order) "
        "VALUES (%s, %s, %s, %s, 1, 100) "
        "ON DUPLICATE KEY UPDATE name=%s, cli_command=%s, description=%s",
        (tool_id, name, cli_command, description, name, cli_command, description),
    )
    return {"id": tool_id, "name": name, "cli_command": cli_command}


async def list_tasks(tenant_id: str = "default", limit: int = 50) -> list[dict]:
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT id, agent_tool_id, agent_tool_name, model_id, model_name, "
        "task_instruction, status, exit_code, error_message, created_at, completed_at "
        "FROM agent_tasks WHERE tenant_id=%s ORDER BY created_at DESC LIMIT %s",
        (tenant_id, limit),
    )
    items = []
    for r in (rows or []):
        items.append({
            "id": r["id"],
            "agent_tool_id": r["agent_tool_id"],
            "agent_tool_name": r.get("agent_tool_name") or "",
            "model_id": r["model_id"],
            "model_name": r.get("model_name") or "",
            "task_instruction": r.get("task_instruction") or "",
            "status": r["status"],
            "exit_code": r.get("exit_code"),
            "error_message": r.get("error_message") or "",
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
            "completed_at": str(r["completed_at"]) if r.get("completed_at") else "",
        })
    return items


async def get_task(task_id: str) -> dict | None:
    mysql = get_mysql_client()
    r = await mysql.fetchone(
        "SELECT id, agent_tool_id, agent_tool_name, model_id, model_name, "
        "task_instruction, status, output, error_message, pid, exit_code, created_at, completed_at "
        "FROM agent_tasks WHERE id=%s",
        (task_id,),
    )
    if not r:
        return None
    return {
        "id": r["id"],
        "agent_tool_id": r["agent_tool_id"],
        "agent_tool_name": r.get("agent_tool_name") or "",
        "model_id": r["model_id"],
        "model_name": r.get("model_name") or "",
        "task_instruction": r.get("task_instruction") or "",
        "status": r["status"],
        "output": r.get("output") or "",
        "error_message": r.get("error_message") or "",
        "pid": r.get("pid"),
        "exit_code": r.get("exit_code"),
        "created_at": str(r["created_at"]) if r.get("created_at") else "",
        "completed_at": str(r["completed_at"]) if r.get("completed_at") else "",
    }


async def get_task_output(task_id: str) -> str:
    redis = get_redis_client()
    cached = await redis.get(f"agent:output:{task_id}")
    if cached:
        return cached
    mysql = get_mysql_client()
    r = await mysql.fetchone("SELECT output FROM agent_tasks WHERE id=%s", (task_id,))
    return (r.get("output") or "") if r else ""


# ---- CLI execution ----

def _build_command(cli_template: str, task: str, model: str) -> list[str]:
    parts = shlex.split(cli_template)
    cmd = []
    for p in parts:
        p = p.replace("{task}", task)
        p = p.replace("{model}", model)
        cmd.append(p)
    return cmd


async def execute_task(
    task_id: str,
    agent_tool_id: str,
    agent_tool_name: str,
    cli_template: str,
    model_id: str,
    model_name: str,
    task_instruction: str,
    env_var_name: str,
    api_key: str,
    base_url: str,
):
    mysql = get_mysql_client()
    redis = get_redis_client()
    await mysql.execute(
        "UPDATE agent_tasks SET status='running' WHERE id=%s",
        (task_id,),
    )

    cmd = _build_command(cli_template, task_instruction, model_id)
    env = os.environ.copy()
    if api_key:
        env[env_var_name] = api_key
    if base_url:
        env[f"{env_var_name}_BASE_URL"] = base_url

    output_lines: list[str] = []
    error_msg = ""
    exit_code = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(os.getcwd()),
        )
        _running_tasks[task_id] = proc
        await mysql.execute("UPDATE agent_tasks SET pid=%s WHERE id=%s", (proc.pid, task_id))
        logger.info("agent_task_started", task_id=task_id, pid=proc.pid, cmd=cmd[0])

        stdout_buf = bytearray()
        stderr_buf = bytearray()

        async def _read_stream(stream, buf, prefix=""):
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                text = chunk.decode(errors="replace")
                lines = text.split("\n")
                for line in lines:
                    if line.strip():
                        output_lines.append(f"{prefix}{line}")
                # Stream to Redis every chunk
                combined = buf.decode(errors="replace")
                await redis.set(f"agent:output:{task_id}", combined, ex=86400)

        await asyncio.gather(
            _read_stream(proc.stdout, stdout_buf),
            _read_stream(proc.stderr, stderr_buf, prefix="[stderr] "),
        )
        exit_code = await proc.wait()

        full_output = stdout_buf.decode(errors="replace")
        if stderr_buf:
            error_text = stderr_buf.decode(errors="replace")
            full_output += "\n" + error_text if full_output else error_text
            if exit_code != 0:
                error_msg = error_text[:2000]

    except FileNotFoundError:
        error_msg = f"命令未找到: {cmd[0]}。请确认该 Agent 工具已安装并在 PATH 中。"
        full_output = error_msg
        exit_code = -1
    except asyncio.CancelledError:
        error_msg = "任务已取消"
        full_output = "\n".join(output_lines) + "\n[任务已取消]"
        exit_code = -2
        proc = _running_tasks.get(task_id)
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
    except Exception as e:
        error_msg = f"执行异常: {e}"
        full_output = "\n".join(output_lines) + f"\n{error_msg}"
        exit_code = -3
    finally:
        _running_tasks.pop(task_id, None)

    status = "completed" if exit_code == 0 else ("failed" if exit_code not in (0, -2) else "cancelled")
    await mysql.execute(
        "UPDATE agent_tasks SET status=%s, output=%s, error_message=%s, exit_code=%s, completed_at=NOW() WHERE id=%s",
        (status, full_output[:65535], error_msg[:2000], exit_code, task_id),
    )
    await redis.set(f"agent:output:{task_id}", full_output[:65535], ex=86400)
    logger.info("agent_task_finished", task_id=task_id, status=status, exit_code=exit_code)


async def cancel_task(task_id: str) -> bool:
    proc = _running_tasks.get(task_id)
    if proc:
        try:
            proc.kill()
        except Exception:
            pass
        return True
    return False


async def create_task(
    tenant_id: str,
    agent_tool_id: str,
    agent_tool_name: str,
    cli_template: str,
    model_id: str,
    model_name: str,
    task_instruction: str,
    env_var_name: str,
    api_key: str,
    base_url: str,
) -> str:
    mysql = get_mysql_client()
    task_id = f"agent-{uuid.uuid4().hex[:12]}"
    await mysql.execute(
        "INSERT INTO agent_tasks (id, tenant_id, agent_tool_id, agent_tool_name, model_id, model_name, task_instruction, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')",
        (task_id, tenant_id, agent_tool_id, agent_tool_name, model_id, model_name, task_instruction),
    )
    asyncio.create_task(
        execute_task(
            task_id, agent_tool_id, agent_tool_name, cli_template,
            model_id, model_name, task_instruction, env_var_name, api_key, base_url,
        )
    )
    return task_id


# ===== Scheduled / recurring tasks =====

async def create_scheduled_task(
    tenant_id: str,
    agent_tool_id: str,
    agent_tool_name: str,
    cli_template: str,
    model_id: str,
    model_name: str,
    task_instruction: str,
    env_var_name: str,
    api_key: str,
    base_url: str,
    interval_seconds: int,
) -> dict:
    mysql = get_mysql_client()
    sched_id = f"sched-{uuid.uuid4().hex[:12]}"
    await mysql.execute(
        "INSERT INTO agent_scheduled_tasks "
        "(id, tenant_id, agent_tool_id, agent_tool_name, model_id, model_name, task_instruction, "
        "interval_seconds, enabled, next_run_at, run_count) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, NOW(), 0)",
        (sched_id, tenant_id, agent_tool_id, agent_tool_name, model_id, model_name,
         task_instruction, interval_seconds),
    )
    # Store the api_key/cli_template in Redis for the scheduler to use (not in MySQL for security)
    redis = get_redis_client()
    sched_config = {
        "cli_template": cli_template,
        "env_var_name": env_var_name,
        "api_key": api_key,
        "base_url": base_url,
    }
    await redis.set(f"agent:sched:{sched_id}", json.dumps(sched_config), ex=86400 * 365)
    logger.info("scheduled_task_created", sched_id=sched_id, interval=interval_seconds)
    return {"id": sched_id, "interval_seconds": interval_seconds, "enabled": True}


async def list_scheduled_tasks(tenant_id: str = "default") -> list[dict]:
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT id, agent_tool_id, agent_tool_name, model_id, model_name, "
        "task_instruction, interval_seconds, enabled, last_run_at, next_run_at, "
        "last_task_id, run_count, created_at "
        "FROM agent_scheduled_tasks WHERE tenant_id=%s ORDER BY created_at DESC",
        (tenant_id,),
    )
    items = []
    for r in (rows or []):
        items.append({
            "id": r["id"],
            "agent_tool_id": r["agent_tool_id"],
            "agent_tool_name": r.get("agent_tool_name") or "",
            "model_id": r["model_id"],
            "model_name": r.get("model_name") or "",
            "task_instruction": r.get("task_instruction") or "",
            "interval_seconds": r.get("interval_seconds") or 3600,
            "enabled": bool(r.get("enabled")),
            "last_run_at": str(r["last_run_at"]) if r.get("last_run_at") else "",
            "next_run_at": str(r["next_run_at"]) if r.get("next_run_at") else "",
            "last_task_id": r.get("last_task_id") or "",
            "run_count": r.get("run_count") or 0,
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
        })
    return items


async def toggle_scheduled_task(sched_id: str, enabled: bool) -> bool:
    mysql = get_mysql_client()
    if enabled:
        await mysql.execute(
            "UPDATE agent_scheduled_tasks SET enabled=1, next_run_at=NOW() WHERE id=%s",
            (sched_id,),
        )
    else:
        await mysql.execute(
            "UPDATE agent_scheduled_tasks SET enabled=0 WHERE id=%s",
            (sched_id,),
        )
    return True


async def delete_scheduled_task(sched_id: str) -> bool:
    mysql = get_mysql_client()
    redis = get_redis_client()
    await mysql.execute("DELETE FROM agent_scheduled_tasks WHERE id=%s", (sched_id,))
    await redis.delete(f"agent:sched:{sched_id}")
    return True


async def run_scheduled_tasks_checker():
    """Background coroutine: check every 30s for due scheduled tasks and execute them."""
    mysql = get_mysql_client()
    redis = get_redis_client()
    logger.info("scheduled_task_checker_started")
    while True:
        try:
            due_rows = await mysql.fetchall(
                "SELECT id, tenant_id, agent_tool_id, agent_tool_name, model_id, model_name, "
                "task_instruction, interval_seconds "
                "FROM agent_scheduled_tasks "
                "WHERE enabled=1 AND next_run_at <= NOW()",
            )
            for row in (due_rows or []):
                sched_id = row["id"]
                # Load config from Redis
                config_raw = await redis.get(f"agent:sched:{sched_id}")
                if not config_raw:
                    logger.warning("scheduled_task_config_missing", sched_id=sched_id)
                    continue
                config = json.loads(config_raw)
                # Create and launch the actual task
                task_id = await create_task(
                    tenant_id=row.get("tenant_id", "default"),
                    agent_tool_id=row["agent_tool_id"],
                    agent_tool_name=row.get("agent_tool_name") or "",
                    cli_template=config.get("cli_template", ""),
                    model_id=row["model_id"],
                    model_name=row.get("model_name") or "",
                    task_instruction=row.get("task_instruction") or "",
                    env_var_name=config.get("env_var_name", "API_KEY"),
                    api_key=config.get("api_key", ""),
                    base_url=config.get("base_url", ""),
                )
                # Update schedule
                await mysql.execute(
                    "UPDATE agent_scheduled_tasks "
                    "SET last_run_at=NOW(), next_run_at=DATE_ADD(NOW(), INTERVAL %s SECOND), "
                    "last_task_id=%s, run_count=run_count+1 WHERE id=%s",
                    (row["interval_seconds"], task_id, sched_id),
                )
                logger.info("scheduled_task_triggered", sched_id=sched_id, task_id=task_id)
        except Exception as e:
            logger.error("scheduled_task_checker_error", error=str(e))
        await asyncio.sleep(30)
