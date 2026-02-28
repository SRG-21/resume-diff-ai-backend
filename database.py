"""
MongoDB Atlas Database Connection Module
Handles async MongoDB connection using Motor driver
"""
import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# Global database client and database instances
_client: Optional[AsyncIOMotorClient] = None
_database: Optional[AsyncIOMotorDatabase] = None


async def connect_to_mongodb() -> None:
    """
    Connect to MongoDB Atlas
    Should be called on application startup
    """
    global _client, _database
    
    if not settings.MONGODB_URI:
        raise ValueError("MONGODB_URI is not configured. Please set it in .env file.")
    
    try:
        logger.info("Connecting to MongoDB Atlas...")
        
        _client = AsyncIOMotorClient(
            settings.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
        )
        
        # Verify connection
        await _client.admin.command('ping')
        
        _database = _client[settings.MONGODB_DATABASE]
        
        # Create indexes for users collection
        await _create_indexes()
        
        logger.info(f"Connected to MongoDB Atlas - Database: {settings.MONGODB_DATABASE}")
        
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        logger.error(f"Failed to connect to MongoDB Atlas: {e}")
        raise


async def close_mongodb_connection() -> None:
    """
    Close MongoDB connection
    Should be called on application shutdown
    """
    global _client, _database
    
    if _client:
        logger.info("Closing MongoDB connection...")
        _client.close()
        _client = None
        _database = None
        logger.info("MongoDB connection closed")


async def _create_indexes() -> None:
    """
    Create necessary indexes for collections
    """
    if _database is None:
        return
    
    # Users collection indexes
    users = _database.users
    await users.create_index("email", unique=True)
    await users.create_index("created_at")
    
    # Blacklisted tokens collection indexes (with TTL for auto-cleanup)
    blacklisted_tokens = _database.blacklisted_tokens
    await blacklisted_tokens.create_index("token", unique=True)
    await blacklisted_tokens.create_index(
        "created_at", 
        expireAfterSeconds=60 * 60 * 24 * 8  # Auto-delete after 8 days
    )
    
    logger.info("Database indexes created/verified")


def get_database() -> AsyncIOMotorDatabase:
    """
    Get the database instance
    Raises error if not connected
    """
    if _database is None:
        raise RuntimeError("Database not connected. Call connect_to_mongodb() first.")
    return _database


def get_users_collection() -> AsyncIOMotorCollection:
    """Get the users collection"""
    return get_database().users


def get_blacklisted_tokens_collection() -> AsyncIOMotorCollection:
    """Get the blacklisted tokens collection"""
    return get_database().blacklisted_tokens
