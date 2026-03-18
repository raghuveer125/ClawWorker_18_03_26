"""Synapse - Neural Message Bus for AI Engineering Hub.

Central communication layer connecting all agents across layers.
Inspired by brain synapses - enables loose coupling and easy scaling.
"""
from .synapse_core import Synapse, SynapseMessage, MessagePriority, get_synapse
from .channels.channel import SynapseChannel, ChannelType
from .context.context_manager import SynapseContextManager
from .coordination.agent_coordinator import AgentCoordinator

__all__ = [
    "Synapse",
    "SynapseMessage",
    "MessagePriority",
    "get_synapse",
    "SynapseChannel",
    "ChannelType",
    "SynapseContextManager",
    "AgentCoordinator",
]
