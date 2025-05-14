import asyncio
import streamlit as st
import pandas as pd
import threading
import aiohttp
from datetime import datetime, timedelta

# Configuration
# Solana Configuration
from blockchair import BLOCKCHAIR_API_KEY, BLOCKCHAIR_API_BASE, SOLANA_RPC_ENDPOINT
from blockchair import call_solana_rpc, call_blockchair_api
from blockchair import get_account_info, get_signatures_for_address, get_transaction, get_token_largest_accounts
PUMPFUN_PROGRAM_ID = "PFUNzK5Ej2iLfBiuYCGDPHih1ZJUzPCCoHn9CiwYtWK"  # PumpFun Program ID

# Initialize session state
if 'tracked_token' not in st.session_state:
    st.session_state.tracked_token = None
if 'tracked_wallets' not in st.session_state:
    st.session_state.tracked_wallets = []
if 'token_trades' not in st.session_state:
    st.session_state.token_trades = []
if 'wallet_history' not in st.session_state:
    st.session_state.wallet_history = {}
if 'monitor_thread' not in st.session_state:
    st.session_state.monitor_thread = None
if 'stop_monitoring' not in st.session_state:
    st.session_state.stop_monitoring = False
if 'last_update' not in st.session_state:
    st.session_state.last_update = None
if 'token_info' not in st.session_state:
    st.session_state.token_info = {}

# Helper Functions
def run_async(coro):
    """Run async functions in Streamlit"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    except Exception as e:
        st.error(f"Error in async operation: {e}")
        return None, None, {}
    finally:
        loop.close()

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

def start_monitoring_thread(token_mint: str):
    """Start monitoring in a separate thread"""
    def monitor_wrapper():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(monitor_token(token_mint))
    
    monitor_thread = threading.Thread(target=monitor_wrapper)
    monitor_thread.daemon = True
    monitor_thread.start()
    return monitor_thread

# New Solana RPC Functions
async def get_solana_token_info(token_mint: str):
    """Get token metadata from Solana RPC API"""
    try:
        # Using our blockchair helper function
        data = await get_account_info(token_mint)
        
        if data and "result" in data and data["result"] and "value" in data["result"]:
            account_data = data["result"]["value"]
            if "data" in account_data and "parsed" in account_data["data"]:
                info = account_data["data"]["parsed"]["info"]
                token_info = {
                    "symbol": info.get("symbol", "Unknown"),
                    "name": info.get("name", "Unknown"),
                    "contract": token_mint,
                    "decimals": info.get("decimals", 9),
                    "supply": info.get("supply", "0")
                }
                return token_info
                
        # Try using Blockchair specific endpoints if available
        token_data = await call_blockchair_api(f"dashboards/token/{token_mint}")
        if token_data and "data" in token_data and token_mint in token_data["data"]:
            token_info = token_data["data"][token_mint]
            return {
                "symbol": token_info.get("symbol", "Unknown"),
                "name": token_info.get("name", "Unknown"),
                "contract": token_mint,
                "decimals": token_info.get("decimals", 9),
                "supply": token_info.get("supply", "0")
            }
            
        return {"symbol": "Unknown", "name": "Unknown", "contract": token_mint, "decimals": 9, "supply": "0"}
    except Exception as e:
        st.error(f"Error fetching token info: {e}")
        return {"symbol": "Unknown", "name": "Unknown", "contract": token_mint, "decimals": 9, "supply": "0"}

async def get_pumpfun_token_transactions(token_mint: str):
    """Get token transactions from Solana RPC using signatures for address"""
    try:
        # Using our blockchair helper function for signature lookup
        transactions = []
        
        # Try BlockChair specific API first
        token_txs = await call_blockchair_api(f"dashboards/token/{token_mint}/transactions", {"limit": 50})
        if token_txs and "data" in token_txs and token_mint in token_txs["data"]:
            # Process BlockChair specific transaction format
            pass  # Would implement Blockchair-specific format handling here
        
        # If BlockChair specific endpoint doesn't work or we need more data, use RPC
        data = await get_signatures_for_address(token_mint, 50)
        
        if data and "result" in data and data["result"]:
            signatures = [item["signature"] for item in data["result"]]
            
            # Get transaction details for each signature
            for signature in signatures:
                tx_data = await get_transaction(signature)
                
                if tx_data and "result" in tx_data and tx_data["result"]:
                    try:
                        tx_result = tx_data["result"]
                        block_time = tx_result.get("blockTime", 0)
                        timestamp = datetime.fromtimestamp(block_time).strftime("%Y-%m-%d %H:%M:%S")
                        
                        # Get transaction message and account keys
                        tx_message = tx_result.get("transaction", {}).get("message", {})
                        account_keys = tx_message.get("accountKeys", [])
                        
                        # Extract relevant information
                        sender = account_keys[0]["pubkey"] if account_keys else "Unknown"
                        receiver = None
                        amount = 0
                        
                        # Try to extract transfer info from instructions
                        meta = tx_result.get("meta", {})
                        post_balances = meta.get("postBalances", [])
                        pre_balances = meta.get("preBalances", [])
                        
                        # Check for token balance changes
                        post_token_balances = meta.get("postTokenBalances", [])
                        pre_token_balances = meta.get("preTokenBalances", [])
                    if "result" in data and data["result"]:
                        signatures = [item["signature"] for item in data["result"]]
                        
                        # Get transaction details for each signature
                        for signature in signatures:
                            tx_payload = {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [
                                    signature,
                                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                                ]
                            }
                            
                            async with session.post(SOLANA_RPC_ENDPOINT, json=tx_payload) as tx_response:
                                if tx_response.status == 200:
                                    tx_data = await tx_response.json()
                                    if "result" in tx_data and tx_data["result"]:
                                        try:
                                            tx_result = tx_data["result"]
                                            block_time = tx_result.get("blockTime", 0)
                                            timestamp = datetime.fromtimestamp(block_time).strftime("%Y-%m-%d %H:%M:%S")
                                            
                                            # Get transaction message and account keys
                                            tx_message = tx_result.get("transaction", {}).get("message", {})
                                            account_keys = tx_message.get("accountKeys", [])
                                            
                                            # Extract relevant information
                                            sender = account_keys[0]["pubkey"] if account_keys else "Unknown"
                                            receiver = None
                                            amount = 0
                                            
                                            # Try to extract transfer info from instructions
                                            meta = tx_result.get("meta", {})
                                            post_balances = meta.get("postBalances", [])
                                            pre_balances = meta.get("preBalances", [])
                                            
                                            # Check for token balance changes
                                            post_token_balances = meta.get("postTokenBalances", [])
                                            pre_token_balances = meta.get("preTokenBalances", [])
                                            
                                            # If we have token balances, try to calculate the amount transferred
                                            if post_token_balances and pre_token_balances:
                                                # Group by owner
                                                by_owner = {}
                                                
                                                # Process pre balances
                                                for balance in pre_token_balances:
                                                    if balance.get("mint") == token_mint:
                                                        owner = balance.get("owner", "Unknown")
                                                        ui_amount = float(balance.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                                                        by_owner[owner] = {"pre": ui_amount, "post": 0}
                                                
                                                # Process post balances
                                                for balance in post_token_balances:
                                                    if balance.get("mint") == token_mint:
                                                        owner = balance.get("owner", "Unknown")
                                                        ui_amount = float(balance.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                                                        if owner in by_owner:
                                                            by_owner[owner]["post"] = ui_amount
                                                        else:
                                                            by_owner[owner] = {"pre": 0, "post": ui_amount}
                                                
                                                # Find significant balance changes
                                                for owner, balances in by_owner.items():
                                                    diff = balances["post"] - balances["pre"]
                                                    if diff > 0:  # This owner received tokens
                                                        receiver = owner
                                                        amount = abs(diff)
                                                    elif diff < 0:  # This owner sent tokens
                                                        sender = owner
                                                        amount = abs(diff)
                                            
                                            # Process instructions to find transfers
                                            if amount == 0:
                                                instructions = tx_result.get("meta", {}).get("innerInstructions", [])
                                                if instructions:
                                                    for inner in instructions:
                                                        for instruction in inner.get("instructions", []):
                                                            if "parsed" in instruction and instruction["parsed"].get("type") == "transfer":
                                                                info = instruction["parsed"]["info"]
                                                                if "amount" in info:
                                                                    # For regular SOL transfers
                                                                    try:
                                                                        amount = float(info.get("amount", 0)) / 1_000_000_000  # Convert from lamports to SOL
                                                                    except ValueError:
                                                                        amount = 0
                                                                elif "tokenAmount" in info:
                                                                    # For SPL token transfers
                                                                    amount = float(info.get("tokenAmount", {}).get("uiAmount", 0) or 0)
                                                                
                                                                receiver = info.get("destination") or receiver
                                            
                                            # Process PumpFun-specific instructions if applicable
                                            is_pump_tx = PUMPFUN_PROGRAM_ID in [acc["pubkey"] for acc in account_keys] if account_keys else False
                                            
                                            tx_hash = signature
                                            trade_data = {
                                                'timestamp': timestamp,
                                                'tx_hash': tx_hash,
                                                'wallet': sender,
                                                'receiver': receiver if receiver else "Unknown",
                                                'amount': amount,
                                                'price': 0,  # Will need market data to determine price
                                                'value': 0,  # Will need market data to determine USD value
                                                'is_buy': False,
                                                'type': 'pumpfun_tx' if is_pump_tx else 'transfer',
                                                'block_height': tx_result.get("slot", 0)
                                            }
                                            transactions.append(trade_data)
                                            
                                            # Keep track of wallets involved
                                            if sender and sender not in st.session_state.tracked_wallets:
                                                st.session_state.tracked_wallets.append(sender)
                                            if receiver and receiver not in st.session_state.tracked_wallets:
                                                st.session_state.tracked_wallets.append(receiver)
                                                
                                        except Exception as e:
                                            st.error(f"Error processing transaction {signature}: {e}")
            return transactions
    except Exception as e:
        st.error(f"Error fetching PumpFun transactions: {e}")
        return []

async def get_token_holders(token_mint: str):
    """Get token holder information from Solana RPC"""
    try:
        # Check if we already have cached data to avoid repeated calls
        if 'holder_cache' not in st.session_state:
            st.session_state.holder_cache = {}
            
        # Get from cache if available and less than 5 minutes old
        cache_key = f"{token_mint}_holders"
        if cache_key in st.session_state.holder_cache:
            cached_data = st.session_state.holder_cache[cache_key]
            cache_time = cached_data.get("timestamp", None)
            if cache_time and (datetime.now() - cache_time).total_seconds() < 300:  # 5 minutes cache
                return cached_data.get("holders", [])
        
        # Show a spinner while loading holders
        with st.spinner("Loading token holders..."):
            async with aiohttp.ClientSession() as session:
                # Get largest accounts with a custom timeout to prevent hanging
                timeout = aiohttp.ClientTimeout(total=15)  # 15 second timeout
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenLargestAccounts",
                    "params": [token_mint]
                }
                
                holders = []                try:
                    # Using our helper function for token holders
                    data = await get_token_largest_accounts(token_mint)
                            
                            # Check for errors in response
                            if "error" in data:
                                error_msg = data['error'].get('message', 'Unknown error')
                                st.error(f"API Error: {error_msg}")
                                return [{"address": "API Error", "amount": 0, "value_usd": 0}]
                            
                            if "result" in data and "value" in data["result"]:
                                token_accounts = data["result"]["value"]
                                
                                # Limit accounts to process to improve performance
                                MAX_ACCOUNTS = 10  # Only get top 10 holders for better performance
                                accounts_to_process = token_accounts[:MAX_ACCOUNTS]
                                
                                # Process accounts in parallel for better performance
                                async def process_account(account):
                                    account_address = account["address"]
                                    acc_payload = {
                                        "jsonrpc": "2.0",
                                        "id": 1,
                                        "method": "getAccountInfo",
                                        "params": [
                                            account_address,
                                            {"encoding": "jsonParsed"}
                                        ]
                                    }
                                      try:
                                        # Using our helper function for account info
                                        acc_data = await get_account_info(account_address)
                                                
                                                if "error" in acc_data:
                                                    return None
                                                    
                                                if "result" in acc_data and acc_data["result"] and "value" in acc_data["result"]:
                                                    value_data = acc_data["result"]["value"]
                                                    
                                                    if not value_data.get("data", {}).get("program", "") == "spl-token":
                                                        return None
                                                        
                                                    parsed_data = value_data.get("data", {}).get("parsed", {})
                                                    if "info" in parsed_data:
                                                        info = parsed_data["info"]
                                                        owner = info.get("owner", "Unknown")
                                                        
                                                        token_amount = info.get("tokenAmount", {})
                                                        if isinstance(token_amount, dict):
                                                            amount = float(token_amount.get("uiAmount", 0) or 0)
                                                        else:
                                                            amount = 0
                                                            
                                                        if amount > 0:
                                                            return {
                                                                "address": owner,
                                                                "amount": amount,
                                                                "value_usd": 0
                                                            }
                                            return None
                                    except Exception:
                                        return None
                                
                                # Process accounts in parallel with asyncio.gather
                                holder_results = await asyncio.gather(*[process_account(account) for account in accounts_to_process], 
                                                                    return_exceptions=False)
                                
                                # Filter out Nones and process results
                                holders = [holder for holder in holder_results if holder is not None]
                                
                                # Process holders by combining duplicate addresses
                                processed_holders = {}
                                for holder in holders:
                                    address = holder["address"]
                                    if address in processed_holders:
                                        processed_holders[address]["amount"] += holder["amount"]
                                    else:
                                        processed_holders[address] = holder
                                
                                # Convert back to list
                                final_holders = list(processed_holders.values())
                                
                                # Cache the result for future use
                                st.session_state.holder_cache[cache_key] = {
                                    "holders": final_holders,
                                    "timestamp": datetime.now()
                                }
                                
                                if not final_holders:
                                    # Return a placeholder for empty results
                                    return [{"address": "No holders found", "amount": 0, "value_usd": 0}]
                                
                                return final_holders
                            else:
                                # Debug: Show what data we got
                                return [{"address": "Invalid response", "amount": 0, "value_usd": 0}]
                except asyncio.TimeoutError:
                    st.warning("Token holder request timed out. Try again later.")
                    return [{"address": "Request timed out", "amount": 0, "value_usd": 0}]
                except Exception as e:
                    st.error(f"Error fetching token holders: {str(e)}")
                    return [{"address": "Error occurred", "amount": 0, "value_usd": 0}]
                
                # Fallback return in case we missed any condition
                return [{"address": "No data available", "amount": 0, "value_usd": 0}]
    except Exception as e:
        st.error(f"Error in holder function: {e}")
        return [{"address": "Error in processing", "amount": 0, "value_usd": 0}]

async def get_wallet_solana_transactions(wallet_address: str):
    """Get wallet transaction history using Solana RPC with timeout handling"""
    try:
        # Create timeout for the session to prevent hanging
        timeout = aiohttp.ClientTimeout(total=10)  # 10 second timeout
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    wallet_address,
                    {"limit": 10}  # Reduced from 20 to 10 for faster loading
                ]
            }
              transactions = []
            
            # Using our helper function for wallet transactions
            data = await get_signatures_for_address(wallet_address, 10)
            if "result" in data and data["result"]:
                signatures = [item["signature"] for item in data["result"]]
                
                # Process signatures in smaller batches for better responsiveness
                for signature in signatures[:5]:  # Only process the 5 most recent transactions initially
                    try:
                        tx_data = await get_transaction(signature)
                                        if "result" in tx_data and tx_data["result"]:
                                            tx_result = tx_data["result"]
                                            
                                            # Basic transaction info
                                            block_time = tx_result.get("blockTime", 0)
                                            timestamp = datetime.fromtimestamp(block_time).strftime("%Y-%m-%d %H:%M:%S")
                                            
                                            # Extract token information if available
                                            currency = "SOL"  # Default to SOL
                                            amount = 0
                                            receiver = "Unknown"
                                            
                                            # Simplified token balance processing for better performance
                                            meta = tx_result.get("meta", {})
                                            
                                            # Look for fast SOL transfer identification first
                                            post_balances = meta.get("postBalances", [])
                                            pre_balances = meta.get("preBalances", [])
                                            
                                            if post_balances and pre_balances:
                                                # Get transaction message and account keys
                                                tx_message = tx_result.get("transaction", {}).get("message", {})
                                                account_keys = tx_message.get("accountKeys", [])
                                                
                                                if account_keys:
                                                    # Find wallet index in account keys
                                                    wallet_idx = -1
                                                    for idx, acc in enumerate(account_keys):
                                                        if acc.get("pubkey") == wallet_address:
                                                            wallet_idx = idx
                                                            break
                                                    
                                                    if wallet_idx >= 0 and wallet_idx < len(pre_balances) and wallet_idx < len(post_balances):
                                                        sol_diff = (post_balances[wallet_idx] - pre_balances[wallet_idx]) / 1_000_000_000  # lamports to SOL
                                                        
                                                        if abs(sol_diff) > 0.0001:  # Only significant transfers
                                                            amount = abs(sol_diff)
                                                            currency = "SOL"
                                                            
                                                            # Simplified receiver detection for performance
                                                            if sol_diff < 0:
                                                                for idx, (pre, post) in enumerate(zip(pre_balances, post_balances)):
                                                                    if idx != wallet_idx and post - pre > 0:
                                                                        receiver = account_keys[idx]["pubkey"]
                                                                        break
                                                            else:
                                                                receiver = wallet_address
                                                                
                                            # Only check token balances if no SOL transfer was found
                                            if amount == 0:
                                                # Quick check for token transfers related to our tracked token
                                                post_token_balances = meta.get("postTokenBalances", [])
                                                pre_token_balances = meta.get("preTokenBalances", [])
                                                
                                                for pre in pre_token_balances:
                                                    if pre.get("owner") == wallet_address:
                                                        for post in post_token_balances:
                                                            if post.get("owner") == wallet_address and post.get("mint") == pre.get("mint"):
                                                                pre_amount = float(pre.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                                                                post_amount = float(post.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                                                                diff = post_amount - pre_amount
                                                                
                                                                if abs(diff) > 0:
                                                                    amount = abs(diff)
                                                                    currency = post.get("mint", "Unknown Token")
                                                                    receiver = wallet_address if diff > 0 else "Unknown"
                                                                    break
                                            
                                            # Create transaction record if we found any transfer
                                            if amount > 0:
                                                tx_type = "receive" if receiver == wallet_address else "send"
                                                tx = {
                                                    'hash': signature,
                                                    'timestamp': timestamp,
                                                    'currency': currency[:5] if len(currency) > 5 and currency != "SOL" else currency,
                                                    'amount': amount,
                                                    'receiver': receiver,
                                                    'value_usd': 0,
                                                    'block_height': tx_result.get("slot", 0),
                                                    'tx_type': tx_type
                                                }
                                                transactions.append(tx)
                            except asyncio.TimeoutError:
                                st.warning(f"Transaction {signature} timed out. Skipping.")
                                continue
                            except Exception as e:
                                st.warning(f"Error processing transaction {signature}: {e}")
                                continue
            
            # If we couldn't find any transactions, return a placeholder
            if not transactions:
                # Return a placeholder to show something
                return [{
                    'hash': 'placeholder',
                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'currency': 'No recent activity',
                    'amount': 0,
                    'receiver': wallet_address,
                    'value_usd': 0,
                    'block_height': 0,
                    'tx_type': 'info'
                }]
                
            return transactions
    except asyncio.TimeoutError:
        st.warning(f"Request for wallet {wallet_address} transactions timed out.")
        return [{
            'hash': 'timeout',
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'currency': 'Request timed out',
            'amount': 0,
            'receiver': wallet_address,
            'value_usd': 0,
            'block_height': 0,
            'tx_type': 'error'
        }]
    except Exception as e:
        st.error(f"Error fetching wallet data: {e}")
        return [{
            'hash': 'error',
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'currency': 'Error loading data',
            'amount': 0,
            'receiver': wallet_address,
            'value_usd': 0,
            'block_height': 0,
            'tx_type': 'error'
        }]

async def get_wallet_transactions_with_prices(wallet_address: str, token_mint: str = None):
    """Get wallet transactions and enrich with price data"""
    transactions = await get_wallet_solana_transactions(wallet_address)
    
    # Get price data for the tokens in these transactions
    prices = {}
    
    # For our tracked token, use the price from our main function
    if token_mint:
        token_price = await get_token_price(token_mint)
        prices[token_mint] = token_price
    
    # For SOL, we can get a more accurate price
    try:
        # Simple placeholder - in a real implementation, fetch from CoinGecko or similar
        prices["SOL"] = 78.50  # Example SOL price in USD
    except Exception:
        prices["SOL"] = 0
    
    # Enrich transactions with price data
    for tx in transactions:
        currency = tx.get('currency')
        if currency == "SOL" and "SOL" in prices:
            tx['value_usd'] = tx.get('amount', 0) * prices["SOL"]
        elif currency in prices:
            tx['value_usd'] = tx.get('amount', 0) * prices[currency]
        elif token_mint and len(currency) > 10:  # This might be a token mint address
            tx['value_usd'] = tx.get('amount', 0) * prices.get(token_mint, 0)
    
    return transactions

async def get_token_price(token_mint: str):
    """Get token price data from CoinGecko or similar API"""
    # For demonstration purposes, we'll use a placeholder
    # In a real implementation, you would integrate with CoinGecko, CoinMarketCap, or Jupiter API
    try:
        # This would be a real API call in production
        return 0.01  # Placeholder price in USD
    except Exception as e:
        st.error(f"Error fetching price data: {e}")
        return 0

async def enrich_token_data(transactions, holders, token_price):
    """Add price and value data to transactions and holders"""
    # Enrich transaction data with price and value
    for tx in transactions:
        tx['price'] = token_price
        tx['value'] = tx['amount'] * token_price
    
    # Enrich holder data with value in USD
    for holder in holders:
        holder['value_usd'] = holder['amount'] * token_price
    
    return transactions, holders

async def process_solana_data(token_mint: str):
    """Process Solana data into a format compatible with our app"""
    token_info = await get_solana_token_info(token_mint)
    trades = await get_pumpfun_token_transactions(token_mint)
    holders = await get_token_holders(token_mint)
    token_price = await get_token_price(token_mint)
    trades, holders = await enrich_token_data(trades, holders, token_price)
    
    # Add holder addresses to tracked wallets list
    for holder in holders:
        holder_address = holder.get("address")
        if holder_address and holder_address != "Unknown" and holder_address not in st.session_state.tracked_wallets:
            st.session_state.tracked_wallets.append(holder_address)
    
    return trades, holders, token_info

# Modified monitor function to use Solana data
async def monitor_token(token_mint: str):
    """Polling-based monitoring for token activity using Solana RPC"""
    try:
        trades, holders, token_info = await process_solana_data(token_mint)
        if token_info:
            st.session_state.token_info = token_info
            
        for trade in trades:
            if not any(t['tx_hash'] == trade['tx_hash'] for t in st.session_state.token_trades):
                st.session_state.token_trades.append(trade)
        
        st.session_state.last_update = datetime.now()
        
        while st.session_state.tracked_token == token_mint and not st.session_state.stop_monitoring:
            await asyncio.sleep(30)
            
            if st.session_state.stop_monitoring:
                break
                
            new_trades, new_holders, new_token_info = await process_solana_data(token_mint)
            
            existing_hashes = {t['tx_hash'] for t in st.session_state.token_trades}
            for trade in new_trades:
                if trade['tx_hash'] not in existing_hashes:
                    st.session_state.token_trades.append(trade)
                    existing_hashes.add(trade['tx_hash'])
            
            st.session_state.last_update = datetime.now()
            
            if new_token_info:
                st.session_state.token_info = new_token_info
            
    except Exception as e:
        st.error(f"Monitoring error: {e}")

# UI Layout
st.set_page_config(page_title="PumpFun Token Scanner", layout="wide")
st.title("🚀 PumpFun Token Scanner")
st.markdown("Track PumpFun token activity and wallet transactions on Solana")

# Sidebar for token input
with st.sidebar:
    st.header("Token Scanner")
    token_address = st.text_input("Enter Token Address", help="The token mint address on Solana")
    
    if st.button("Start Tracking"):
        if token_address:
            st.session_state.tracked_token = token_address
            st.session_state.stop_monitoring = False
            st.session_state.token_trades = []
            st.session_state.token_info = {}
            st.session_state.tracked_wallets = []
            st.success(f"Tracking token: {token_address[:6]}...{token_address[-4:]}")
            
            if st.session_state.monitor_thread and st.session_state.monitor_thread.is_alive():
                st.session_state.stop_monitoring = True
                st.session_state.monitor_thread.join(timeout=1)
            st.session_state.monitor_thread = start_monitoring_thread(token_address)
        else:
            st.error("Please enter a valid token address")
    
    if st.session_state.tracked_token:
        if st.button("Stop Tracking"):
            st.session_state.stop_monitoring = True
            if st.session_state.monitor_thread and st.session_state.monitor_thread.is_alive():
                st.session_state.monitor_thread.join(timeout=2)
            st.session_state.tracked_token = None
            st.session_state.tracked_wallets = []
            st.session_state.monitor_thread = None
            st.session_state.last_update = None
            st.info("Stopped tracking")
              # Add PumpFun specific options
    st.header("PumpFun Options")
    st.checkbox("Track PumpFun Events Only", value=True, 
                help="When checked, only show transactions related to the PumpFun protocol")
      st.markdown("---")
    st.caption(f"Using Blockchair Solana API (Key: {BLOCKCHAIR_API_KEY[:5]}...)")
    
    # Test Blockchair API connection
    if st.button("Test API Connection"):
        with st.spinner("Testing Blockchair API connection..."):
            from blockchair import test_blockchair_connection
            success, message = run_async(test_blockchair_connection())
            if success:
                st.success(message)
            else:
                st.error(message)
    
    st.info("Blockchair API provides enhanced Solana transaction tracking with faster response times and improved reliability.")

# Main content tabs
tab1, tab2, tab3 = st.tabs(["Token Overview", "Wallet Activity", "Transaction History"])

# Token Overview Tab
with tab1:
    if st.session_state.tracked_token:
        if st.session_state.token_info:
            token_display = (
                f"{st.session_state.token_info.get('symbol', 'Unknown')} "
                f"({st.session_state.tracked_token[:6]}...{st.session_state.tracked_token[-4:]})"
            )
        else:
            token_display = f"{st.session_state.tracked_token[:6]}...{st.session_state.tracked_token[-4:]}"
        
        st.header(f"Token: {token_display}")
        
        if st.session_state.last_update:
            st.caption(f"Last updated: {st.session_state.last_update.strftime('%Y-%m-%d %H:%M:%S')}")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Recent Activity")
            if st.session_state.token_trades:
                data_sources = ["All Sources", "PumpFun Events", "Transfers"]
                selected_source = st.selectbox("Data Source", data_sources)
                
                df_trades = pd.DataFrame(st.session_state.token_trades[-50:])
                
                if selected_source == "PumpFun Events":
                    df_trades = df_trades[df_trades['type'] == 'pumpfun_tx']
                elif selected_source == "Transfers":
                    df_trades = df_trades[df_trades['type'] == 'transfer']
                
                if not df_trades.empty:
                    df_trades['amount'] = df_trades['amount'].apply(lambda x: f"{x:,.2f}")
                    df_trades['price'] = df_trades['price'].apply(lambda x: f"{x:,.6f}" if x > 0 else "N/A")
                    df_trades['value'] = df_trades['value'].apply(lambda x: f"${x:,.2f}" if x > 0 else "N/A")
                    
                    st.dataframe(
                        df_trades.sort_values('timestamp', ascending=False),
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "timestamp": "Time",
                            "tx_hash": st.column_config.TextColumn("TX Hash", width="medium"),
                            "wallet": st.column_config.TextColumn("Wallet", width="medium"),
                            "receiver": st.column_config.TextColumn("Receiver", width="medium"),
                            "amount": "Amount",
                            "price": "Price",
                            "value": "Value",
                            "is_buy": "Is Buy",
                            "type": "Type"
                        }
                    )
            else:
                st.info("No trades recorded yet")
        
        with col2:
            st.subheader("Token Holders")
            
            try:
                # Get token price for value calculations
                token_price = run_async(get_token_price(st.session_state.tracked_token))
                
                # Get fresh holder data
                holders = run_async(get_token_holders(st.session_state.tracked_token))
                
                # Enrich holder data with token price
                for holder in holders:
                    holder['value_usd'] = holder['amount'] * token_price
                
                if holders:
                    st.metric("Total Holders", len(holders))
                    
                    # Sort holders by amount (largest first)
                    sorted_holders = sorted(holders, key=lambda x: x['amount'], reverse=True)
                    
                    df_holders = pd.DataFrame(sorted_holders[:10])
                    df_holders['Wallet'] = df_holders['address'].apply(
                        lambda x: f"{x[:4]}...{x[-4:]}" if isinstance(x, str) else x
                    )
                    
                    df_holders['Formatted Amount'] = df_holders['amount'].apply(
                        lambda x: f"{x:,.2f}" if x >= 1 else f"{x:,.8f}"
                    )
                    
                    df_holders['Value USD'] = df_holders['value_usd'].apply(
                        lambda x: f"${x:,.2f}" if x > 0 else "N/A"
                    )
                    
                    st.dataframe(
                        df_holders[['Wallet', 'Formatted Amount', 'Value USD']],
                        hide_index=True,
                        use_container_width=True
                    )
                    
                    if len(holders) > 10:
                        st.download_button(
                            label="Download All Holders",
                            data=pd.DataFrame(holders).to_csv(index=False).encode('utf-8'),
                            file_name=f"{st.session_state.token_info.get('symbol', 'token')}_holders.csv",
                            mime='text/csv'
                        )
                else:
                    st.warning("No holders found. This may be a new token.")
                
            except Exception as e:
                st.error(f"Error fetching holders: {str(e)}")

# Wallet Activity Tab
with tab2:
    if st.session_state.tracked_token:
        st.header("Wallet Activity")
        
        if st.session_state.tracked_wallets:
            selected_wallet = st.selectbox(
                "Select Wallet to Inspect",
                st.session_state.tracked_wallets,
                format_func=lambda x: f"{x[:6]}...{x[-4:]}" if isinstance(x, str) and len(x) > 10 else x
            )
            
            if selected_wallet:
                try:
                    transactions = run_async(get_wallet_transactions_with_prices(selected_wallet, st.session_state.tracked_token))
                    st.session_state.wallet_history[selected_wallet] = transactions
                    
                    st.subheader(f"Wallet: {selected_wallet[:6]}...{selected_wallet[-4:]}" if isinstance(selected_wallet, str) and len(selected_wallet) > 10 else selected_wallet)
                    
                    if transactions:
                        st.write(f"Recent Transactions ({len(transactions)}):")
                        df_txs = pd.DataFrame(transactions)
                        
                        df_txs['amount'] = df_txs['amount'].apply(lambda x: f"{x:,.2f}")
                        df_txs['value_usd'] = df_txs['value_usd'].apply(lambda x: f"${x:,.2f}" if x > 0 else "N/A")
                        
                        st.dataframe(
                            df_txs,
                            hide_index=True,
                            use_container_width=True,
                            column_config={
                                "timestamp": "Time",
                                "hash": st.column_config.TextColumn("TX Hash", width="medium"),
                                "currency": "Token",
                                "amount": "Amount",
                                "receiver": st.column_config.TextColumn("Receiver", width="medium"),
                                "value_usd": "Value USD",
                                "block_height": "Block"
                            }
                        )
                    else:
                        st.info("No transactions found for this wallet")
                    
                    wallet_trades = [t for t in st.session_state.token_trades 
                                   if t.get('wallet') == selected_wallet or t.get('receiver') == selected_wallet]
                    if wallet_trades:
                        st.subheader("Token-Specific Activity")
                        df_wallet_trades = pd.DataFrame(wallet_trades)
                        
                        df_wallet_trades['amount'] = df_wallet_trades['amount'].apply(lambda x: f"{x:,.2f}")
                        df_wallet_trades['price'] = df_wallet_trades['price'].apply(lambda x: f"{x:,.6f}" if x > 0 else "N/A")
                        df_wallet_trades['value'] = df_wallet_trades['value'].apply(lambda x: f"${x:,.2f}" if x > 0 else "N/A")
                        
                        st.dataframe(
                            df_wallet_trades.sort_values('timestamp', ascending=False),
                            hide_index=True,
                            use_container_width=True,
                            column_config={
                                "timestamp": "Time",
                                "tx_hash": st.column_config.TextColumn("TX Hash", width="medium"),
                                "wallet": st.column_config.TextColumn("Sender", width="medium"),
                                "receiver": st.column_config.TextColumn("Receiver", width="medium"),
                                "amount": "Amount",
                                "price": "Price",
                                "value": "Value",
                                "is_buy": "Is Buy",
                                "type": "Type"
                            }
                        )
                except Exception as e:
                    st.error(f"Error fetching wallet history: {e}")
        else:
            st.info("No wallets detected yet. Waiting for activity...")
    else:
        st.info("Track a token to see wallet activity")

# Transaction History Tab
with tab3:
    if st.session_state.tracked_token:
        st.header("Transaction History")
        
        if st.session_state.token_trades:
            df_all_trades = pd.DataFrame(st.session_state.token_trades)
            
            col1, col2, col3 = st.columns(3)
            with col1:
                min_value = st.number_input("Minimum Value (USD)", min_value=0.0, value=0.0, step=1.0)
            with col2:
                show_pumpfun = st.checkbox("Show PumpFun Events", value=True)
            with col3:
                show_transfers = st.checkbox("Show Transfers", value=True)
            
            # For PumpFun events, we might not have accurate value data yet
            filtered = df_all_trades
            if min_value > 0:
                filtered = df_all_trades[df_all_trades['value'] >= min_value]
            
            filters = []
            if show_pumpfun:
                filters.append(filtered['type'] == 'pumpfun_tx')
            if show_transfers:
                filters.append(filtered['type'] == 'transfer')
            
            if filters:
                filtered = filtered[pd.concat(filters, axis=1).any(axis=1)]
            else:
                filtered = pd.DataFrame()
            
            if not filtered.empty:
                filtered['amount'] = filtered['amount'].apply(lambda x: f"{x:,.2f}")
                filtered['price'] = filtered['price'].apply(lambda x: f"{x:,.6f}" if x > 0 else "N/A")
                filtered['value'] = filtered['value'].apply(lambda x: f"${x:,.2f}" if x > 0 else "N/A")
                
                st.dataframe(
                    filtered.sort_values('timestamp', ascending=False),
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "timestamp": "Time",
                        "tx_hash": st.column_config.TextColumn("TX Hash", width="medium"),
                        "wallet": st.column_config.TextColumn("Wallet", width="medium"),
                        "receiver": st.column_config.TextColumn("Receiver", width="medium"),
                        "amount": "Amount",
                        "price": "Price",
                        "value": "Value",
                        "is_buy": "Is Buy",
                        "type": "Type",
                        "block_height": "Block"
                    }
                )
                
                st.subheader("Trade Statistics")
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Transactions", len(filtered))
                
                # For PumpFun transactions, we might need different metrics
                tx_by_type = filtered['type'].value_counts().to_dict()
                pumpfun_count = tx_by_type.get('pumpfun_tx', 0)
                transfer_count = tx_by_type.get('transfer', 0)
                
                col2.metric("PumpFun Events", pumpfun_count)
                col3.metric("Transfers", transfer_count)
                
                st.download_button(
                    label="Download Transaction History",
                    data=filtered.to_csv(index=False).encode('utf-8'),
                    file_name=f"{st.session_state.token_info.get('symbol', 'token')}_transactions.csv",
                    mime='text/csv'
                )
            else:
                st.info("No transactions match your filter criteria")
        else:
            st.info("No trades recorded yet")
    else:
        st.info("Track a token to see transaction history")
