import asyncio
import streamlit as st
import json
import pandas as pd
import threading
import websockets
import aiohttp
from datetime import datetime
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient


# Configuration
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
PUMP_FUN_WS = "wss://pumpportal.fun/api/data"
PUMP_FUN_PROGRAM_ID = "PUMPFiWb4agfPrT3VfW5aQyPEB6jNv9QmJ5L5Y8NjqHu"

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
            st.session_state.tracked_token = token_address
            st.session_state.stop_monitoring = False
            st.success(f"Tracking token: {token_address[:6]}...{token_address[-4:]}")
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
            st.info("Stopped tracking")

async def get_token_holders(token_mint: str):
    """Get token holders using Helius API"""
    HELIUS_API_KEY = "7604d74d-42ff-4316-b5f4-ed1ad1544505"  # Replace with your actual API key
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
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                data = await response.json()
                
                if 'error' in data:
                    print(f"Helius API error: {data['error']}")
                    return []
                
                accounts = data.get('result', {}).get('token_accounts', [])
                
                holders = []
                for account in accounts:
                    if 'owner' in account and 'amount' in account:
                        holders.append({
                            'address': account['owner'],
                            'amount': account['amount'],
                            'ui_amount': account.get('uiAmount', account['amount'] / (10 ** account.get('decimals', 9)))
                        })
                
                # Sort by largest holders first
                holders.sort(key=lambda x: x['amount'], reverse=True)
                return holders
                
    except Exception as e:
        print(f"Error fetching holders from Helius: {e}")
        return []

# Modified display code for col2
with col2:
    st.subheader("Token Holders (Helius API)")
    try:
        holders = run_async(get_token_holders(st.session_state.tracked_token))
        st.metric("Total Holders", len(holders))
        
        if holders:
            # Create and display the dataframe
            df_holders = pd.DataFrame(holders[:15])  # Show top 15 holders
            
            # Format display
            df_holders['Wallet'] = df_holders['address'].apply(
                lambda x: f"{x[:6]}...{x[-4:]}" if len(x) > 10 else x
            )
            
            df_holders['Balance'] = df_holders['ui_amount'].apply(
                lambda x: f"{x:,.2f}" if x >= 1 else f"{x:.6f}".rstrip('0').rstrip('.') if '.' in f"{x:.6f}" else f"{x:,.0f}"
            )
            
            st.dataframe(
                df_holders[['Wallet', 'Balance']],
                column_config={
                    "Wallet": st.column_config.TextColumn("Wallet", width="medium"),
                    "Balance": st.column_config.TextColumn("Token Balance")
                },
                hide_index=True,
                use_container_width=True,
                height=min(400, 35 * len(df_holders) + 40)
            )
            
            # Download button
            csv = df_holders[['address', 'amount']].rename(
                columns={'address': 'Wallet', 'amount': 'RawAmount'}
            ).to_csv(index=False)
            
            st.download_button(
                label="Download Full Holder Data",
                data=csv,
                file_name=f"{st.session_state.tracked_token[:10]}_holders.csv",
                mime='text/csv'
            )
        else:
            st.warning("No holders found or API limit reached")
            
    except Exception as e:
        st.error(f"Error: {str(e)}")
        st.info("Note: You need a valid Helius API key for this functionality")
        
async def get_wallet_history(wallet: str):
    """Get transaction history for a wallet"""
    client = AsyncClient(SOLANA_RPC)
    txs = await client.get_signatures_for_address(
        Pubkey.from_string(wallet),
        limit=50
    )
    await client.close()
    return [str(tx.signature) for tx in txs.value]

async def track_token_wallets(token_mint: str):
    """Find wallets trading a specific token"""
    client = AsyncClient(SOLANA_RPC)
    program_id = Pubkey.from_string(PUMP_FUN_PROGRAM_ID)
    
    response = await client.get_signatures_for_address(
        Pubkey.from_string(token_mint),
        limit=100
    )
    
    wallets = set()
    for tx in response.value:
        tx_details = await client.get_transaction(tx.signature)
        if tx_details.value:
            for instruction in tx_details.value.transaction.transaction.message.instructions:
                if instruction.program_id == program_id and instruction.accounts:
                    trader = str(instruction.accounts[0])
                    wallets.add(trader)
    
    await client.close()
    return list(wallets)

async def monitor_token(token_mint: str):
    """WebSocket monitoring for token trades"""
    try:
        async with websockets.connect(PUMP_FUN_WS) as websocket:
            # Subscribe to token trades
            await websocket.send(json.dumps({
                "method": "subscribeTokenTrade",
                "keys": [token_mint]
            }))
            
            while st.session_state.tracked_token == token_mint and not st.session_state.stop_monitoring:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=1)
                    data = json.loads(message)
                    
                    if data.get('type') == 'tokenTrade' and data['token'] == token_mint:
                        # Add to trade history
                        trade_data = {
                            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            'tx_hash': data.get('txId', ''),
                            'wallet': data.get('account', ''),
                            'amount': data.get('amount', 0),
                            'price': data.get('price', 0),
                            'value': data.get('value', 0),
                            'is_buy': data.get('amount', 0) > 0
                        }
                        st.session_state.token_trades.append(trade_data)
                        
                        # Update wallet list
                        if data['account'] not in st.session_state.tracked_wallets:
                            st.session_state.tracked_wallets.append(data['account'])
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    st.error(f"WebSocket error: {e}")
                    break
    except Exception as e:
        st.error(f"Connection error: {e}")

def run_async(coro):
    """Run async functions in Streamlit"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
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

# Main content tabs
tab1, tab2, tab3 = st.tabs(["Token Overview", "Wallet Activity", "Transaction History"])

# Token Overview Tab
with tab1:
    if st.session_state.tracked_token:
        st.header(f"Token: {st.session_state.tracked_token[:6]}...{st.session_state.tracked_token[-4:]}")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Recent Activity")
            if st.session_state.token_trades:
                df_trades = pd.DataFrame(st.session_state.token_trades[-10:])
                st.dataframe(df_trades.sort_values('timestamp', ascending=False), hide_index=True)
            else:
                st.info("No trades recorded yet")
        with col2:
            st.subheader("Token Holders")
            try:
                holders = run_async(get_token_holders(st.session_state.tracked_token))
                st.metric("Total Holders", len(holders))
                
                if holders:
                    # Create a nicer dataframe display
                    df_holders = pd.DataFrame(holders[:10])  # Show top 10 holders
                    
                    # Format the amounts
                    df_holders['Formatted Amount'] = df_holders['ui_amount'].apply(
                        lambda x: f"{x:,.2f}" if x >= 1 else f"{x:,.6f}"
                    )
                    
                    # Display with wallet shortening
                    df_holders['Wallet'] = df_holders['address'].apply(
                        lambda x: f"{x[:4]}...{x[-4:]}"
                    )
                    
                    # Show in a nicer table
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
                    
                    # Add download button
                    st.download_button(
                        label="Download All Holders",
                        data=df_holders.to_csv(index=False).encode('utf-8'),
                        file_name=f"{st.session_state.tracked_token[:5]}_holders.csv",
                        mime='text/csv'
                    )
                else:
                    st.warning("No holders found. This may be a new token.")
                    
            except Exception as e:
                st.error(f"Error fetching holders: {str(e)}")
                st.error("Please ensure:")
                st.error("1. You're using a valid Pump.fun token address")
                st.error("2. The token has existing holders")
                st.error(f"Technical details: {e}")

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
                try:
                    history = run_async(get_wallet_history(selected_wallet))
                    st.session_state.wallet_history[selected_wallet] = history
                    
                    st.subheader(f"Wallet: {selected_wallet[:6]}...{selected_wallet[-4:]}")
                    st.write(f"Recent Transactions ({len(history)}):")
                    st.write(history[:10])
                    
                    # Show wallet's trades for the tracked token
                    wallet_trades = [t for t in st.session_state.token_trades if t['wallet'] == selected_wallet]
                    if wallet_trades:
                        st.subheader("Token-Specific Trades")
                        df_wallet_trades = pd.DataFrame(wallet_trades)
                        st.dataframe(df_wallet_trades, hide_index=True)
                except Exception as e:
                    st.error(f"Error fetching wallet history: {e}")
        else:
            st.info("No wallets detected yet. Waiting for trades...")
    else:
        st.info("Track a token to see wallet activity")

# Transaction History Tab
with tab3:
    if st.session_state.tracked_token:
        st.header("Transaction History")
        
        if st.session_state.token_trades:
            df_all_trades = pd.DataFrame(st.session_state.token_trades)
            
            # Filters
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
            
            st.dataframe(filtered.sort_values('timestamp', ascending=False), hide_index=True)
            
            # Stats
            st.subheader("Trade Statistics")
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Trades", len(df_all_trades))
            col2.metric("Total Buy Volume", 
                       f"{df_all_trades[df_all_trades['is_buy']]['value'].sum():.2f} SOL")
            col3.metric("Total Sell Volume", 
                       f"{df_all_trades[~df_all_trades['is_buy']]['value'].sum():.2f} SOL")
        else:
            st.info("No trades recorded yet")
    else:
        st.info("Track a token to see transaction history")

# Start monitoring when a token is selected
if st.session_state.tracked_token and not st.session_state.monitor_thread:
    st.session_state.monitor_thread = start_monitoring_thread(st.session_state.tracked_token)
