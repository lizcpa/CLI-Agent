from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncGenerator, Optional

import aio_pika
from aio_pika import ExchangeType, IncomingMessage, RobustConnection, RobustChannel


DEFAULT_URL = "amqp://guest:guest@localhost:5672//"

PREDEFINED_QUEUES = [
    "crawl_queue",
    "analyze_queue",
    "ai_queue",
    "compose_queue",
    "publish_queue",
]


class RabbitMQClient:
    _instance: Optional[RabbitMQClient] = None
    _lock = asyncio.Lock()

    def __new__(cls) -> RabbitMQClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connection: Optional[RobustConnection] = None
            cls._instance._channel: Optional[RobustChannel] = None
        return cls._instance

    @property
    def connection(self) -> Optional[RobustConnection]:
        return self._connection

    @property
    def channel(self) -> Optional[RobustChannel]:
        return self._channel

    async def connect(self, url: str | None = None) -> RobustConnection:
        url = url or os.getenv("RABBITMQ_URL", DEFAULT_URL)
        async with self._lock:
            if self._connection is None or self._connection.is_closed:
                self._connection = await aio_pika.connect_robust(url)
                self._channel = await self._connection.channel()
                await self._channel.set_qos(prefetch_count=1)
        return self._connection

    async def close(self) -> None:
        async with self._lock:
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
                self._connection = None
                self._channel = None

    async def _ensure_channel(self) -> RobustChannel:
        if self._channel is None or self._channel.is_closed:
            await self.connect()
        return self._channel

    async def declare_queue(
        self,
        name: str,
        durable: bool = True,
        arguments: dict[str, Any] | None = None,
    ) -> aio_pika.Queue:
        ch = await self._ensure_channel()
        queue_args = arguments or {}
        if "x-dead-letter-exchange" not in queue_args:
            queue_args["x-dead-letter-exchange"] = f"{name}.dlx"
        if "x-dead-letter-routing-key" not in queue_args:
            queue_args["x-dead-letter-routing-key"] = f"{name}.dead"
        queue = await ch.declare_queue(name, durable=durable, arguments=queue_args)
        dlx_exchange = await ch.declare_exchange(
            f"{name}.dlx", ExchangeType.DIRECT, durable=True
        )
        dead_queue = await ch.declare_queue(
            f"{name}.dead", durable=True
        )
        await dead_queue.bind(dlx_exchange, routing_key=f"{name}.dead")
        return queue

    async def declare_exchange(
        self, name: str, type: str = "direct", durable: bool = True
    ) -> aio_pika.Exchange:
        ch = await self._ensure_channel()
        exchange_type = getattr(ExchangeType, type.upper(), ExchangeType.DIRECT)
        return await ch.declare_exchange(name, exchange_type, durable=durable)

    async def bind_queue(
        self, queue_name: str, exchange_name: str, routing_key: str
    ) -> None:
        ch = await self._ensure_channel()
        queue = await ch.declare_queue(queue_name, durable=True)
        exchange = await ch.declare_exchange(
            exchange_name, ExchangeType.DIRECT, durable=True
        )
        await queue.bind(exchange, routing_key=routing_key)

    async def publish(
        self,
        exchange: str = "",
        routing_key: str = "",
        message_dict: dict[str, Any] | None = None,
    ) -> None:
        ch = await self._ensure_channel()
        if message_dict is None:
            message_dict = {}
        exch = await ch.declare_exchange(
            exchange, ExchangeType.DIRECT, durable=True
        ) if exchange else ch.default_exchange
        body = json.dumps(message_dict, ensure_ascii=False).encode()
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await exch.publish(message, routing_key=routing_key)

    async def consume(self, queue_name: str) -> AsyncGenerator[IncomingMessage, None]:
        ch = await self._ensure_channel()
        queue = await ch.declare_queue(queue_name, durable=True)
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                yield message

    async def ack(self, message: IncomingMessage) -> None:
        await message.ack()

    async def nack(self, message: IncomingMessage) -> None:
        await message.nack(requeue=True)

    async def reject(self, message: IncomingMessage) -> None:
        await message.reject(requeue=False)

    async def get_queue_length(self, queue_name: str) -> int:
        ch = await self._ensure_channel()
        queue = await ch.declare_queue(queue_name, durable=True, passive=True)
        return queue.declaration_result.message_count

    async def purge_queue(self, queue_name: str) -> int:
        ch = await self._ensure_channel()
        queue = await ch.declare_queue(queue_name, durable=True)
        purged = await queue.purge()
        return purged.message_count

    async def setup_predefined_queues(self) -> None:
        for queue_name in PREDEFINED_QUEUES:
            await self.declare_queue(queue_name)


rabbitmq_client = RabbitMQClient()
