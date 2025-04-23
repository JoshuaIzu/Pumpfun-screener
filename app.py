import asyncio
import streamlit as st
import json
import pandas as pd
import threading
import websockets
import aiohttp
from datetime import datetime
import time
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from streamlit.runtime.scriptrunner import add_script_run_ctx

# Configuration
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
PUMP_FUN_WS = "wss://pumpportal.fun/api/data"
PUMP_FUN_PROGRAM_ID = "PUMPFiWb4agfPrT3VfW5aQyPEB6jNv9QmJ5L5Y8NjqHu"
HELIUS_API_KEY = "7604d74d-42ff-4316-b5f4-ed1ad1544505"  # Replace with your actual key

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
if 'websocket_connected' not in st.session_state:
    st.session_state.websocket_connected = False
if 'latest_error' not in st.session_state:
    st.session_state.latest_error = None

# Page layout
st.set_page_config(page_title="Pump.fun Token Scanner", layout="wide")
st.title("🚀 Pump.fun Token Scanner")
st.markdown("Track token activity and wallet transactions on Pump.fun")

# Sidebar for token input
with st.sidebar:
    st.header("Token Scanner")
    token_address = st.text_input("Enter Pump.fun Token Address", help="The token mint address on Solana")
    
    if st.button("Start Tracking"):
        if token_address:
            try:
                # Validate the token address
                Pubkey.from_string(token_address)
                st.session_state.tracked_token = token_address
                st.session_state.stop_monitoring = False
                st.session_state.token_trades = []  # Reset trades list
                st.success(f"Tracking token: {token_address[:6]}...{token_address[-4:]}")
            except Exception as e:
                st.error(f"Invalid token address: {str(e)}")
        else:
            st.error("Please enter a valid token address")
    
    if st.session_state.tracked_token:
        if st.button("Stop Tracking"):
            st.session_state.stop_monitoring = True
            st.info("Stopping tracking... Please wait")

    # Show connection status
    if st.session_state.tracked_token:
        if st.session_state.websocket_connected:
            st.success("WebSocket: Connected")
        else:
            st.warning("WebSocket: Disconnected")
    
    # Show latest error if any
    if st.session_state.latest_error:
        with st.expander("Recent Error"):
            st.error(st.session_state.latest_error)
            if st.button("Clear Error"):
                st.session_state.latest_error = None

async def get_token_holders(token_mint: str):
    """Get token holders using Helius API with retry mechanism"""
    url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "getTokenAccounts",
        "params": [{
            "mint": token_mint,
            "page": 1,
            "limit": 1000,
            "displayOptions": {
                "showZeroBalance": False
            }
        }]
    }
    
    # Implement retry with exponential backoff
    max_retries = 3
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status == 429:  # Too Many Requests
                        retry_after = int(response.headers.get('Retry-After', retry_delay))
                        await asyncio.sleep(retry_after)
                        continue
                        
                    data = await response.json()
                    
                    if 'error' in data:
                        error_msg = data['error'].get('message', str(data['error']))
                        if attempt < max_retries - 1:  # Don't wait after the last attempt
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                            continue
                        else:
                            st.session_state.latest_error = f"Helius API error: {error_msg}"
                            return []
                    
                    accounts = data.get('result', {}).get('token_accounts', [])
                    
                    holders = []
                    for account in accounts:
                        if 'owner' in account and 'amount' in account:
                            decimals = account.get('decimals', 9)
                            ui_amount = account.get('uiAmount', float(account['amount']) / (10 ** decimals))
                            holders.append({
                                'address': account['owner'],
                                'amount': account['amount'],
                                'ui_amount': ui_amount
                            })
                    
                    holders.sort(key=lambda x: float(x['amount']), reverse=True)
                    return holders
                
        except aiohttp.ClientError as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                st.session_state.latest_error = f"Network error with Helius API: {str(e)}"
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                st.session_state.latest_error = f"Error fetching holders from Helius: {str(e)}"
    
    return []  # Return empty list after all retries failed

async def get_wallet_history(wallet: str):
    """Get transaction history for a wallet with error handling"""
    try:
        client = AsyncClient(SOLANA_RPC)
        try:
            txs = await client.get_signatures_for_address(
                Pubkey.from_string(wallet),
                limit=50
            )
            return [str(tx.signature) for tx in txs.value]
        except Exception as e:
            st.session_state.latest_error = f"Error fetching wallet history: {str(e)}"
            return []
        finally:
            await client.close()
    except Exception as e:
        st.session_state.latest_error = f"Error with Solana client: {str(e)}"
        return []

async def track_token_wallets(token_mint: str):
    """Find wallets trading a specific token"""
    try:
        client = AsyncClient(SOLANA_RPC)
        try:
            program_id = Pubkey.from_string(PUMP_FUN_PROGRAM_ID)
            
            response = await client.get_signatures_for_address(
                Pubkey.from_string(token_mint),
                limit=100
            )
            
            wallets = set()
            for tx in response.value:
                try:
                    tx_details = await client.get_transaction(tx.signature)
                    if tx_details.value and tx_details.value.transaction and tx_details.value.transaction.transaction:
                        for instruction in tx_details.value.transaction.transaction.message.instructions:
                            if instruction.program_id == program_id and instruction.accounts:
                                trader = str(instruction.accounts[0])
                                wallets.add(trader)
                except Exception as e:
                    # Continue with other transactions if one fails
                    continue
            
            return list(wallets)
        finally:
            await client.close()
    except Exception as e:
        st.session_state.latest_error = f"Error tracking token wallets: {str(e)}"
        return []

async def monitor_token(token_mint: str):
    """WebSocket monitoring for token trades with reconnection logic"""
    backoff_time = 1  # Initial backoff time in seconds
    max_backoff = 60  # Maximum backoff time in seconds
    
    while st.session_state.tracked_token == token_mint and not st.session_state.stop_monitoring:
        try:
            # Update connection status
            st.session_state.websocket_connected = False
            
            # Connect to websocket with timeout
            async with websockets.connect(PUMP_FUN_WS, ping_interval=20, close_timeout=10) as websocket:
                # Update connection status
                st.session_state.websocket_connected = True
                backoff_time = 1  # Reset backoff time on successful connection
                
                # Subscribe to token trades
                await websocket.send(json.dumps({
                    "method": "subscribeTokenTrade",
                    "keys": [token_mint]
                }))
                
                # Keep connection alive and process messages
                while st.session_state.tracked_token == token_mint and not st.session_state.stop_monitoring:
                    try:
                        # Use timeout to allow checking stop_monitoring periodically
                        message = await asyncio.wait_for(websocket.recv(), timeout=1)
                        
                        try:
                            data = json.loads(message)
                            
                            if data.get('type') == 'tokenTrade' and data.get('token') == token_mint:
                                trade_data = {
                                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    'tx_hash': data.get('txId', ''),
                                    'wallet': data.get('account', ''),
                                    'amount': data.get('amount', 0),
                                    'price': data.get('price', 0),
                                    'value': data.get('value', 0),
                                    'is_buy': data.get('amount', 0) > 0
                                }
                                
                                # Add to trades list and wallet list
                                if trade_data not in st.session_state.token_trades:
                                    st.session_state.token_trades.append(trade_data)
                                
                                if data.get('account') and data.get('account') not in st.session_state.tracked_wallets:
                                    st.session_state.tracked_wallets.append(data['account'])
                        except json.JSONDecodeError as e:
                            # Handle invalid JSON
                            continue
                            
                    except asyncio.TimeoutError:
                        # This is expected when using wait_for with timeout
                        # Just continue the loop to check stop_monitoring
                        continue
                    except websockets.ConnectionClosed as e:
                        # Connection closed, try to reconnect
                        st.session_state.websocket_connected = False
                        break
                    except Exception as e:
                        # Unexpected error, break and try to reconnect
                        st.session_state.latest_error = f"WebSocket error: {str(e)}"
                        break
        
        except (websockets.ConnectionError, websockets.InvalidStatusCode, websockets.InvalidURI) as e:
            st.session_state.websocket_connected = False
            st.session_state.latest_error = f"WebSocket connection error: {str(e)}"
        except Exception as e:
            st.session_state.websocket_connected = False
            st.session_state.latest_error = f"Unexpected error: {str(e)}"
        
        # Only attempt to reconnect if we're still supposed to be tracking
        if st.session_state.tracked_token == token_mint and not st.session_state.stop_monitoring:
            # Wait before reconnecting (exponential backoff)
            await asyncio.sleep(backoff_time)
            backoff_time = min(backoff_time * 2, max_backoff)  # Double backoff time with upper limit

def run_async(coro):
    """Run async functions in Streamlit with proper error handling"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.close()
        return result
    except Exception as e:
        st.session_state.latest_error = f"Async execution error: {str(e)}"
        return None

def start_monitoring_thread(token_mint: str):
    """Start monitoring in a separate thread with proper context"""
    def monitor_wrapper():
        try:
            # Add the streamlit script context to this thread
            add_script_run_ctx()
            
            # Set up event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Run the monitoring coroutine
            loop.run_until_complete(monitor_token(token_mint))
            
            # Clean up
            loop.close()
            
            # Update status when thread ends
            st.session_state.websocket_connected = False
            if st.session_state.tracked_token == token_mint:
                st.session_state.tracked_token = None
                st.session_state.monitor_thread = None
        except Exception as e:
            st.session_state.latest_error = f"Monitor thread error: {str(e)}"
    
    # Create and start the thread
    monitor_thread = threading.Thread(target=monitor_wrapper)
    monitor_thread.daemon = True
    monitor_thread.start()
    return monitor_thread

# Main content tabs
tab1, tab2, tab3 = st.tabs(["Token Overview", "Wallet Activity", "Transaction History"])

# Token Overview Tab
with tab1:
    if st.session_state.tracked_token:
        st.header(f"Token: {st.session_state.tracked_token[:6]}...{st.session_state.tracked_token[-4:]}")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Recent Activity")
            activity_placeholder = st.empty()
            
            if st.session_state.token_trades:
                df_trades = pd.DataFrame(st.session_state.token_trades[-10:])
                activity_placeholder.dataframe(df_trades.sort_values('timestamp', ascending=False), hide_index=True)
            else:
                activity_placeholder.info("No trades recorded yet")
        
        with col2:
            st.subheader("Token Holders")
            holders_placeholder = st.empty()
            
            try:
                holders = run_async(get_token_holders(st.session_state.tracked_token))
                
                if holders:
                    holders_placeholder.metric("Total Holders", len(holders))
                    
                    df_holders = pd.DataFrame(holders[:10])
                    df_holders['Formatted Amount'] = df_holders['ui_amount'].apply(
                        lambda x: f"{x:,.2f}" if x >= 1 else f"{x:,.6f}"
                    )
                    df_holders['Wallet'] = df_holders['address'].apply(
                        lambda x: f"{x[:4]}...{x[-4:]}"
                    )
                    
                    st.dataframe(
                        df_holders[['Wallet', 'Formatted Amount']],
                        column_config={
                            "Wallet": st.column_config.TextColumn("Wallet", width="medium"),
                            "Formatted Amount": st.column_config.NumberColumn(
                                "Token Balance", 
                                format="%.6f",
                                width="medium"
                            )
                        },
                        hide_index=True,
                        use_container_width=True
                    )
                    
                    st.download_button(
                        label="Download All Holders",
                        data=df_holders.to_csv(index=False).encode('utf-8'),
                        file_name=f"{st.session_state.tracked_token[:5]}_holders.csv",
                        mime='text/csv'
                    )
                else:
                    holders_placeholder.warning("No holders found. This may be a new token.")
                    
            except Exception as e:
                holders_placeholder.error(f"Error fetching holders: {str(e)}")

# Wallet Activity Tab
with tab2:
    if st.session_state.tracked_token:
        st.header("Wallet Activity")
        
        if st.session_state.tracked_wallets:
            selected_wallet = st.selectbox(
                "Select Wallet to Inspect",
                st.session_state.tracked_wallets,
                format_func=lambda x: f"{x[:6]}...{x[-4:]}"
            )
            
            if selected_wallet:
                wallet_placeholder = st.empty()
                
                try:
                    # Check if we already have the history
                    if selected_wallet in st.session_state.wallet_history:
                        history = st.session_state.wallet_history[selected_wallet]
                    else:
                        history = run_async(get_wallet_history(selected_wallet))
                        st.session_state.wallet_history[selected_wallet] = history
                    
                    wallet_placeholder.subheader(f"Wallet: {selected_wallet[:6]}...{selected_wallet[-4:]}")
                    st.write(f"Recent Transactions ({len(history)}):")
                    
                    if history:
                        # Format the transaction list
                        formatted_txs = [f"[{tx[:8]}...{tx[-4:]}](https://solscan.io/tx/{tx})" for tx in history[:10]]
                        for tx in formatted_txs:
                            st.markdown(tx)
                    else:
                        st.info("No transactions found")
                    
                    # Filter trades for this wallet
                    wallet_trades = [t for t in st.session_state.token_trades if t['wallet'] == selected_wallet]
                    if wallet_trades:
                        st.subheader("Token-Specific Trades")
                        df_wallet_trades = pd.DataFrame(wallet_trades)
                        st.dataframe(df_wallet_trades, hide_index=True)
                    else:
                        st.info("No token trades recorded for this wallet")
                        
                except Exception as e:
                    wallet_placeholder.error(f"Error: {str(e)}")
        else:
            st.info("No wallets detected yet. Waiting for trades...")
    else:
        st.info("Track a token to see wallet activity")

# Transaction History Tab
with tab3:
    if st.session_state.tracked_token:
        st.header("Transaction History")
        
        history_placeholder = st.empty()
        
        if st.session_state.token_trades:
            df_all_trades = pd.DataFrame(st.session_state.token_trades)
            
            col1, col2 = st.columns(2)
            with col1:
                min_value = st.number_input("Minimum Trade Value (SOL)", min_value=0.0, value=1.0)
            with col2:
                show_buys = st.checkbox("Show Buys", value=True)
                show_sells = st.checkbox("Show Sells", value=True)
            
            # Apply filters
            filtered = df_all_trades[df_all_trades['value'] >= min_value]
            if show_buys and not show_sells:
                filtered = filtered[filtered['is_buy']]
            elif show_sells and not show_buys:
                filtered = filtered[~filtered['is_buy']]
            
            history_placeholder.dataframe(filtered.sort_values('timestamp', ascending=False), hide_index=True)
            
            st.subheader("Trade Statistics")
            col1, col2, col3 = st.columns(3)
            
            # Calculate statistics safely
            total_trades = len(df_all_trades)
            buy_volume = df_all_trades[df_all_trades['is_buy']]['value'].sum() if not df_all_trades[df_all_trades['is_buy']].empty else 0
            sell_volume = df_all_trades[~df_all_trades['is_buy']]['value'].sum() if not df_all_trades[~df_all_trades['is_buy']].empty else 0
            
            col1.metric("Total Trades", total_trades)
            col2.metric("Total Buy Volume", f"{buy_volume:.2f} SOL")
            col3.metric("Total Sell Volume", f"{sell_volume:.2f} SOL")
        else:
            history_placeholder.info("No trades recorded yet")
    else:
        history_placeholder.info("Track a token to see transaction history")

# Start monitoring when a token is selected
if st.session_state.tracked_token and not st.session_state.monitor_thread:
    st.session_state.monitor_thread = start_monitoring_thread(st.session_state.tracked_token)
