import asyncio
import streamlit as st
import pandas as pd
import threading
import aiohttp
import solana
from datetime import datetime, timedelta

# Configuration
BITQUERY_API_KEY = "p8HQx2XC5WVzK2dQATvUSjvGY4"  # Keep as backup
BITQUERY_ACCESS_TOKEN = "ory_at_J8RO-utFAeWyhZsPagkoV1yZZUD-GGX_ZQMqEeb9Q6Q.VRWDqmXZFzXzPeZox_865Jxo5m2b3HzGtasWrMacpOQ"
BITQUERY_ENDPOINT = "https://graphql.bitquery.io"

# Solana Configuration
SOLANA_RPC_ENDPOINT = "https://api.mainnet-beta.solana.com"
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
if 'bitquery_last_update' not in st.session_state:
    st.session_state.bitquery_last_update = None
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
        async with aiohttp.ClientSession() as session:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    token_mint,
                    {"encoding": "jsonParsed"}
                ]
            }
            
            async with session.post(SOLANA_RPC_ENDPOINT, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if "result" in data and data["result"] and "value" in data["result"]:
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
                return {"symbol": "Unknown", "name": "Unknown", "contract": token_mint, "decimals": 9, "supply": "0"}
    except Exception as e:
        st.error(f"Error fetching token info: {e}")
        return {"symbol": "Unknown", "name": "Unknown", "contract": token_mint, "decimals": 9, "supply": "0"}

async def get_pumpfun_token_transactions(token_mint: str):
    """Get token transactions from Solana RPC using signatures for address"""
    try:
        async with aiohttp.ClientSession() as session:
            # First get recent signatures for the token mint address
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    token_mint,
                    {"limit": 50}
                ]
            }
            
            transactions = []
            async with session.post(SOLANA_RPC_ENDPOINT, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
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
                                            instructions = tx_result.get("meta", {}).get("innerInstructions", [])
                                            if instructions:
                                                for inner in instructions:
                                                    for instruction in inner.get("instructions", []):
                                                        if "parsed" in instruction and instruction["parsed"].get("type") == "transfer":
                                                            info = instruction["parsed"]["info"]
                                                            amount = float(info.get("amount", 0))
                                                            receiver = info.get("destination")
                                            
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
        async with aiohttp.ClientSession() as session:
            # First, get the token's largest accounts
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [token_mint]
            }
            
            holders = []
            async with session.post(SOLANA_RPC_ENDPOINT, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if "result" in data and "value" in data["result"]:
                        token_accounts = data["result"]["value"]
                        
                        for account in token_accounts:
                            # Get account info for each token account
                            acc_payload = {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getAccountInfo",
                                "params": [
                                    account["address"],
                                    {"encoding": "jsonParsed"}
                                ]
                            }
                            
                            async with session.post(SOLANA_RPC_ENDPOINT, json=acc_payload) as acc_response:
                                if acc_response.status == 200:
                                    acc_data = await acc_response.json()
                                    if "result" in acc_data and acc_data["result"] and "value" in acc_data["result"]:
                                        try:
                                            parsed_data = acc_data["result"]["value"].get("data", {}).get("parsed", {})
                                            if "info" in parsed_data:
                                                info = parsed_data["info"]
                                                owner = info.get("owner", "Unknown")
                                                amount = float(info.get("tokenAmount", {}).get("uiAmount", 0))
                                                
                                                holders.append({
                                                    "address": owner,
                                                    "amount": amount,
                                                    "value_usd": 0  # Would need price data to calculate
                                                })
                                        except Exception as e:
                                            st.error(f"Error processing account {account['address']}: {e}")
            
            if not holders:
                st.warning("No holders found. This may be a new token.")
                
            return holders
    except Exception as e:
        st.error(f"Error fetching holders: {e}")
        return []

async def get_wallet_solana_transactions(wallet_address: str):
    """Get wallet transaction history using Solana RPC"""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    wallet_address,
                    {"limit": 20}
                ]
            }
            
            transactions = []
            async with session.post(SOLANA_RPC_ENDPOINT, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    if "result" in data and data["result"]:
                        signatures = [item["signature"] for item in data["result"]]
                        
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
                                        tx_result = tx_data["result"]
                                        
                                        # Basic transaction info
                                        block_time = tx_result.get("blockTime", 0)
                                        timestamp = datetime.fromtimestamp(block_time).strftime("%Y-%m-%d %H:%M:%S")
                                        
                                        # Extract token information if available
                                        currency = "SOL"  # Default to SOL
                                        amount = 0
                                        receiver = "Unknown"
                                        
                                        # Look for token transfers in the transaction
                                        try:
                                            for instruction in tx_result.get("meta", {}).get("innerInstructions", []):
                                                for inner in instruction.get("instructions", []):
                                                    if "parsed" in inner and inner["parsed"].get("type") == "transfer":
                                                        info = inner["parsed"]["info"]
                                                        amount = float(info.get("amount", 0))
                                                        receiver = info.get("destination", "Unknown")
                                        except:
                                            pass
                                        
                                        tx = {
                                            'hash': signature,
                                            'timestamp': timestamp,
                                            'currency': currency,
                                            'amount': amount,
                                            'receiver': receiver,
                                            'value_usd': 0,  # Would need price data
                                            'block_height': tx_result.get("slot", 0)
                                        }
                                        transactions.append(tx)
            return transactions
    except Exception as e:
        st.error(f"Error fetching wallet data: {e}")
        return []

async def process_solana_data(token_mint: str):
    """Process Solana data into a format compatible with our app"""
    token_info = await get_solana_token_info(token_mint)
    trades = await get_pumpfun_token_transactions(token_mint)
    holders = await get_token_holders(token_mint)
    
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
        
        st.session_state.bitquery_last_update = datetime.now()
        
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
            
            st.session_state.bitquery_last_update = datetime.now()
            
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
            st.session_state.bitquery_last_update = None
            st.info("Stopped tracking")
            
    # Add PumpFun specific options
    st.header("PumpFun Options")
    st.checkbox("Track PumpFun Events Only", value=True, 
                help="When checked, only show transactions related to the PumpFun protocol")
    
    st.markdown("---")
    st.caption("Using Solana Foundation RPC")

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
        
        if st.session_state.bitquery_last_update:
            st.caption(f"Last updated: {st.session_state.bitquery_last_update.strftime('%Y-%m-%d %H:%M:%S')}")
        
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
                _, holders, _ = run_async(process_solana_data(st.session_state.tracked_token))
                
                if holders:
                    st.metric("Total Holders", len(holders))
                    
                    df_holders = pd.DataFrame(holders[:10])
                    df_holders['Wallet'] = df_holders['address'].apply(
                        lambda x: f"{x[:4]}...{x[-4:]}" if isinstance(x, str) else x
                    )
                    
                    df_holders['Formatted Amount'] = df_holders['amount'].apply(
                        lambda x: f"{x:,.2f}" if x >= 1 else f"{x:,.6f}"
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
                    transactions = run_async(get_wallet_solana_transactions(selected_wallet))
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
