"""
Synapse Channels - Named message channels for different concerns.

Channels provide logical separation of message flows:
- data: Market data, quotes, indicators
- cmd: Commands, task dispatches
- status: Health, progress updates
- memory: Memory queries and updates
- alert: Risk alerts, notifications
- learn: Learning signals, feedback
"""

import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger(__name__)


class ChannelType(Enum):
    """Standard channel types."""
    DATA = "data"
    COMMAND = "cmd"
    STATUS = "status"
    MEMORY = "memory"
    ALERT = "alert"
    LEARN = "learn"
    CUSTOM = "custom"


@dataclass
class ChannelConfig:
    """Channel configuration."""
    name: str
    channel_type: ChannelType
    max_buffer: int = 1000
    persist: bool = False
    filter_duplicates: bool = False
    rate_limit: Optional[int] = None  # Messages per second


class SynapseChannel:
    """
    A named channel for message flow.

    Features:
    - Message buffering with configurable size
    - Rate limiting
    - Duplicate filtering
    - Subscriber management
    """

    def __init__(self, config: ChannelConfig):
        self.name = config.name
        self.channel_type = config.channel_type
        self.max_buffer = config.max_buffer
        self.persist = config.persist
        self.filter_duplicates = config.filter_duplicates
        self.rate_limit = config.rate_limit

        self._buffer: deque = deque(maxlen=config.max_buffer)
        self._subscribers: List[Callable] = []
        self._recent_hashes: deque = deque(maxlen=100)  # For duplicate detection
        self._message_count = 0
        self._last_rate_check = 0.0
        self._rate_count = 0

    def subscribe(self, callback: Callable):
        """Subscribe to this channel."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        """Unsubscribe from this channel."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def publish(self, message: Any) -> bool:
        """
        Publish message to channel.

        Returns:
            True if published, False if filtered/rate-limited
        """
        import time

        # Rate limiting
        if self.rate_limit:
            now = time.time()
            if now - self._last_rate_check >= 1.0:
                self._rate_count = 0
                self._last_rate_check = now

            if self._rate_count >= self.rate_limit:
                return False
            self._rate_count += 1

        # Duplicate filtering
        if self.filter_duplicates:
            msg_hash = hash(str(message))
            if msg_hash in self._recent_hashes:
                return False
            self._recent_hashes.append(msg_hash)

        # Buffer message
        self._buffer.append(message)
        self._message_count += 1

        # Notify subscribers
        for subscriber in self._subscribers:
            try:
                subscriber(message)
            except Exception as e:
                logger.error(f"Channel {self.name} subscriber error: {e}")

        return True

    def get_recent(self, count: int = 10) -> List[Any]:
        """Get recent messages from buffer."""
        return list(self._buffer)[-count:]

    def clear(self):
        """Clear message buffer."""
        self._buffer.clear()

    def get_stats(self) -> Dict:
        """Get channel statistics."""
        return {
            "name": self.name,
            "type": self.channel_type.value,
            "buffer_size": len(self._buffer),
            "subscribers": len(self._subscribers),
            "total_messages": self._message_count,
        }


class ChannelManager:
    """Manages multiple Synapse channels."""

    def __init__(self):
        self._channels: Dict[str, SynapseChannel] = {}
        self._create_default_channels()

    def _create_default_channels(self):
        """Create standard channels."""
        defaults = [
            ChannelConfig("data", ChannelType.DATA, max_buffer=5000, filter_duplicates=True),
            ChannelConfig("cmd", ChannelType.COMMAND, max_buffer=1000),
            ChannelConfig("status", ChannelType.STATUS, max_buffer=500, rate_limit=100),
            ChannelConfig("memory", ChannelType.MEMORY, max_buffer=1000),
            ChannelConfig("alert", ChannelType.ALERT, max_buffer=500),
            ChannelConfig("learn", ChannelType.LEARN, max_buffer=2000),
        ]

        for config in defaults:
            self._channels[config.name] = SynapseChannel(config)

    def get_channel(self, name: str) -> Optional[SynapseChannel]:
        """Get channel by name."""
        return self._channels.get(name)

    def create_channel(self, config: ChannelConfig) -> SynapseChannel:
        """Create a new channel."""
        channel = SynapseChannel(config)
        self._channels[config.name] = channel
        return channel

    def list_channels(self) -> List[str]:
        """List all channel names."""
        return list(self._channels.keys())

    def get_all_stats(self) -> Dict[str, Dict]:
        """Get stats for all channels."""
        return {name: ch.get_stats() for name, ch in self._channels.items()}
