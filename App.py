import asyncio
import streamlit as st
import json
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
import websockets
import pandas as pd
from datetime import datetime

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
            st.success(f"Tracking token: {token_address[:6]}...{token_address[-4:]}")
        else:
            st.error("Please enter a valid token address")
    
    if st.session_state.tracked_token:
        if st.button("Stop Tracking"):
            st.session_state.tracked_token = None
            st.session_state.tracked_wallets = []
            st.info("Stopped tracking")

# Main content tabs
tab1, tab2, tab3 = st.tabs(["Token Overview", "Wallet Activity", "Transaction History"])

async def get_token_holders(token_mint: str):
    """Get current token holders"""
    client = AsyncClient(SOLANA_RPC)
    accounts = await client.get_token_accounts_by_owner(
        Pubkey.from_string(token_mint),
        {"programId": Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")}
    )
    return [str(account.pubkey) for account in accounts.value]

async def get_wallet_history(wallet: str):
    """Get transaction history for a wallet"""
    client = AsyncClient(SOLANA_RPC)
    txs = await client.get_signatures_for_address(
        Pubkey.from_string(wallet),
        limit=50
    )
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
    async with websockets.connect(PUMP_FUN_WS) as websocket:
        # Subscribe to token trades
        await websocket.send(json.dumps({
            "method": "subscribeTokenTrade",
            "keys": [token_mint]
        }))
        
        while st.session_state.tracked_token == token_mint:
            message = await websocket.recv()
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
                
                # Refresh the display
                st.rerun()

def run_async(coro):
    """Run async functions in Streamlit"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

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
                st.write(f"Top Holders (sample):")
                st.write(holders[:5])
            except Exception as e:
                st.error(f"Error fetching holders: {e}")
        
        st.subheader("Live Trade Monitor")
        placeholder = st.empty()
        if st.session_state.token_trades:
            last_trade = st.session_state.token_trades[-1]
            with placeholder.container():
                col1, col2, col3 = st.columns(3)
                col1.metric("Last Price", f"{last_trade['price']:.8f} SOL")
                col2.metric("Amount", f"{abs(last_trade['amount']):,.0f}")
                col3.metric("Value", f"{last_trade['value']:.2f} SOL")
                st.caption(f"Wallet: {last_trade['wallet'][:6]}...{last_trade['wallet'][-4:]}")
    else:
        st.info("Enter a token address in the sidebar to begin tracking")

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
if st.session_state.tracked_token:
    if st.session_state.tracked_token and not hasattr(st.session_state, 'monitor_task'):
        async def start_monitoring():
            await monitor_token(st.session_state.tracked_token)
        
        st.session_state.monitor_task = asyncio.create_task(start_monitoring())
