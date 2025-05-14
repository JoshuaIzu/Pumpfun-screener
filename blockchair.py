"""
Blockchair API helper functions for Solana integration
"""

import aiohttp
import asyncio
import streamlit as st
from datetime import datetime

# Configuration
BLOCKCHAIR_API_KEY = "G_DGZfVMJ470wLbOYU4UbJpcEGFzRC"
BLOCKCHAIR_API_BASE = "https://api.blockchair.com/solana"
SOLANA_RPC_ENDPOINT = "https://api.mainnet-beta.solana.com"  # Fallback for RPC methods not supported by Blockchair

async def call_blockchair_api(endpoint, params=None):
    """Call the Blockchair API with the given endpoint and parameters"""
    try:
        url = f"{BLOCKCHAIR_API_BASE}/{endpoint}"
        if params is None:
            params = {}
        
        # Add API key to parameters
        params['key'] = BLOCKCHAIR_API_KEY
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    st.error(f"Blockchair API error: {response.status}")
                    return None
    except Exception as e:
        st.error(f"Error calling Blockchair API: {e}")
        return None

async def call_solana_rpc(method, params):
    """Call Solana RPC API (either via Blockchair or direct fallback)"""
    try:
        # First try through Blockchair's RPC endpoint
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        
        # Try Blockchair first
        endpoint = "rpc"
        blockchair_url = f"{BLOCKCHAIR_API_BASE}/{endpoint}"
        
        async with aiohttp.ClientSession() as session:
            # Add API key to query params instead of URL path
            async with session.post(blockchair_url, params={"key": BLOCKCHAIR_API_KEY}, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if "error" not in data:
                        return data
                
                # If Blockchair fails, fallback to direct Solana RPC
                st.warning(f"Falling back to direct Solana RPC for method: {method}")
                async with session.post(SOLANA_RPC_ENDPOINT, json=payload) as fallback_response:
                    if fallback_response.status == 200:
                        return await fallback_response.json()
                    else:
                        st.error(f"Solana RPC error: {fallback_response.status}")
                        return None
    except Exception as e:
        st.error(f"Error calling RPC: {e}")
        return None

# Convenience functions to wrap RPC calls
async def get_account_info(address, encoding="jsonParsed"):
    """Get account information"""
    return await call_solana_rpc("getAccountInfo", [address, {"encoding": encoding}])

async def get_signatures_for_address(address, limit=50):
    """Get transaction signatures for an address"""
    return await call_solana_rpc("getSignaturesForAddress", [address, {"limit": limit}])

async def get_transaction(signature, encoding="jsonParsed"):
    """Get transaction details by signature"""
    return await call_solana_rpc("getTransaction", [signature, {"encoding": encoding, "maxSupportedTransactionVersion": 0}])

async def get_token_largest_accounts(token_mint):
    """Get largest token accounts"""
    return await call_solana_rpc("getTokenLargestAccounts", [token_mint])

async def test_blockchair_connection():
    """Test connection to Blockchair API and validate API key"""
    try:
        # Call a simple Blockchair endpoint to test connection
        stats = await call_blockchair_api("stats")
        
        if stats and "context" in stats:
            api_info = stats["context"].get("api", {})
            version = api_info.get("version", "Unknown")
            return True, f"Connected successfully to Blockchair API (v{version})"
        else:
            return False, "Connection failed - invalid API key or rate limited"
    except Exception as e:
        return False, f"Connection error: {str(e)}"
        
async def get_solana_block_info():
    """Get the latest Solana block information from Blockchair"""
    try:
        # Call Blockchair's Solana stats endpoint
        stats = await call_blockchair_api("stats")
        
        if stats and "data" in stats:
            data = stats["data"]
            last_block = data.get("blocks", [])[0] if "blocks" in data and data["blocks"] else {}
            
            # If we didn't get block data from stats endpoint, try blocks endpoint
            if not last_block:
                blocks = await call_blockchair_api("blocks", {"limit": 1})
                if blocks and "data" in blocks:
                    last_block = blocks["data"][0] if blocks["data"] else {}
            
            return {
                "block_id": last_block.get("id", "Unknown"),
                "height": last_block.get("height", 0),
                "time": last_block.get("time", "Unknown"),
                "transaction_count": last_block.get("transaction_count", 0)
            }
        
        return {
            "block_id": "Unknown",
            "height": 0,
            "time": "Unknown",
            "transaction_count": 0
        }
    except Exception as e:
        print(f"Error fetching Solana block info: {e}")
        return {
            "block_id": "Error",
            "height": 0,
            "time": "Unknown",
            "transaction_count": 0
        }
