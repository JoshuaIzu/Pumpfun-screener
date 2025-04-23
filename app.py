import asyncio
import streamlit as st
import json
import pandas as pd
import threading
import aiohttp
from datetime import datetime

# Configuration
BITQUERY_API_KEY = "DwqQuqdVYlmrCEdRM_M_LlT3lN"  # Keep as backup
BITQUERY_ACCESS_TOKEN = "ory_at_Ym84gpJTx1UI5Aj8-MdO0hAYBFo-XKa07YLNPiME6HQ.NX4MnUB-YO3hJoDn_cJu6T_7GvhCYG6eXSkrvz6VpDs"
BITQUERY_ENDPOINT = "https://graphql.bitquery.io"

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

# Page layout
st.set_page_config(page_title="Solana Token Scanner", layout="wide")
st.title("🚀 Solana Token Scanner")
st.markdown("Track token activity and wallet transactions on Solana")

# Sidebar for token input
with st.sidebar:
    st.header("Token Scanner")
    token_address = st.text_input("Enter Token Address", help="The token mint address on Solana")
    
    if st.button("Start Tracking"):
        if token_address:
            st.session_state.tracked_token = token_address
            st.session_state.stop_monitoring = False
            st.session_state.token_trades = []  # Reset trades when starting a new token
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
            st.session_state.bitquery_last_update = None
            st.info("Stopped tracking")

async def get_bitquery_token_activity(token_mint: str):
    """Get token activity data from Bitquery GraphQL API"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BITQUERY_ACCESS_TOKEN}"
    }
    
    query = """
    query TokenActivity($token: String!) {
      Solana {
        Transfers(
          where: {Transfer: {Currency: {SmartContract: {is: $token}}}}
          limit: {count: 50}
        ) {
          Transfer {
            Currency {
              Symbol
              Name
              SmartContract
            }
            Amount
            AmountInUSD
            Sender {
              Address
            }
            Receiver {
              Address
            }
            Block {
              Timestamp
            }
            Transaction {
              Hash
            }
          }
        }
        DEXTrades(
          where: {Trade: {Buy: {Currency: {SmartContract: {is: $token}}}}}
          limit: {count: 50}
        ) {
          Trade {
            Dex {
              ProtocolName
              ProgramAddress
            }
            Buy {
              Currency {
                Symbol
                Name
                SmartContract
              }
              Amount
              Price
            }
            Sell {
              Currency {
                Symbol
                Name
              }
              Amount
              Price
            }
            Block {
              Timestamp
            }
            Transaction {
              Hash
            }
          }
        }
        # Query for token holders
        TokenHolders: BalanceUpdates(
          where: {BalanceUpdate: {Currency: {SmartContract: {is: $token}}}}
          orderBy: {descendingByField: "amount"}
          limit: {count: 100}
        ) {
          BalanceUpdate {
            Address
            Amount
            AmountInUSD
          }
        }
      }
    }
    """
    
    variables = {
        "token": token_mint
    }
    
    payload = {
        "query": query,
        "variables": variables
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(BITQUERY_ENDPOINT, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    print(f"Bitquery API error: Status {response.status}")
                    error_text = await response.text()
                    print(f"Error details: {error_text}")
                    return None
    except Exception as e:
        print(f"Error fetching data from Bitquery: {e}")
        return None

async def process_bitquery_data(token_mint: str):
    """Process Bitquery data into a format compatible with our app"""
    data = await get_bitquery_token_activity(token_mint)
    if not data or "data" not in data:
        return [], []
    
    # Process transfers and trades
    trades = []
    holders = []
    
    # Handle transfers
    if "Solana" in data["data"] and "Transfers" in data["data"]["Solana"]:
        for transfer in data["data"]["Solana"]["Transfers"]:
            t = transfer["Transfer"]
            
            # Format the data to match our structure
            trade_data = {
                'timestamp': datetime.fromisoformat(t["Block"]["Timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                'tx_hash': t["Transaction"]["Hash"],
                'wallet': t["Sender"]["Address"],
                'receiver': t["Receiver"]["Address"],
                'amount': float(t["Amount"]),
                'price': 0,  # Transfer doesn't have price
                'value': float(t.get("AmountInUSD", 0)),
                'is_buy': False,  # Transfer is neither buy nor sell, but we need to classify
                'type': 'transfer'
            }
            trades.append(trade_data)
            
            # Add wallets to tracked wallets
            if t["Sender"]["Address"] not in st.session_state.tracked_wallets:
                st.session_state.tracked_wallets.append(t["Sender"]["Address"])
            if t["Receiver"]["Address"] not in st.session_state.tracked_wallets:
                st.session_state.tracked_wallets.append(t["Receiver"]["Address"])
    
    # Handle DEX trades
    if "Solana" in data["data"] and "DEXTrades" in data["data"]["Solana"]:
        for trade in data["data"]["Solana"]["DEXTrades"]:
            t = trade["Trade"]
            
            # Format the data to match our structure
            trade_data = {
                'timestamp': datetime.fromisoformat(t["Block"]["Timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                'tx_hash': t["Transaction"]["Hash"],
                'wallet': "DEX Trade",  # We don't have wallet info directly
                'amount': float(t["Buy"]["Amount"]),
                'price': float(t["Buy"]["Price"]),
                'value': float(t["Buy"]["Amount"]) * float(t["Buy"]["Price"]),
                'is_buy': True,  # DEX trade is always buy in this query
                'type': 'dex_trade',
                'dex': t["Dex"]["ProtocolName"]
            }
            trades.append(trade_data)
    
    # Handle token holders
    if "Solana" in data["data"] and "TokenHolders" in data["data"]["Solana"]:
        for holder in data["data"]["Solana"]["TokenHolders"]:
            h = holder["BalanceUpdate"]
            holder_data = {
                'address': h["Address"],
                'amount': float(h["Amount"]),
                'value_usd': float(h.get("AmountInUSD", 0))
            }
            holders.append(holder_data)
    
    return trades, holders

async def get_wallet_transactions(wallet_address: str):
    """Get transaction history for a wallet using Bitquery"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BITQUERY_ACCESS_TOKEN}"
    }
    
    query = """
    query WalletActivity($address: String!) {
      Solana {
        Transfers(
          where: {Transfer: {Sender: {Address: {is: $address}}}}
          limit: {count: 20}
        ) {
          Transfer {
            Currency {
              Symbol
              Name
              SmartContract
            }
            Amount
            AmountInUSD
            Receiver {
              Address
            }
            Block {
              Timestamp
            }
            Transaction {
              Hash
            }
          }
        }
      }
    }
    """
    
    variables = {
        "address": wallet_address
    }
    
    payload = {
        "query": query,
        "variables": variables
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(BITQUERY_ENDPOINT, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    transactions = []
                    
                    if "data" in data and "Solana" in data["data"] and "Transfers" in data["data"]["Solana"]:
                        for transfer in data["data"]["Solana"]["Transfers"]:
                            t = transfer["Transfer"]
                            tx = {
                                'hash': t["Transaction"]["Hash"],
                                'timestamp': datetime.fromisoformat(t["Block"]["Timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                                'currency': t["Currency"]["Symbol"] or t["Currency"]["SmartContract"][:10],
                                'amount': float(t["Amount"]),
                                'receiver': t["Receiver"]["Address"],
                                'value_usd': float(t.get("AmountInUSD", 0))
                            }
                            transactions.append(tx)
                    
                    return transactions
                else:
                    return []
    except Exception as e:
        print(f"Error fetching wallet data: {e}")
        return []

async def monitor_token(token_mint: str):
    """Polling-based monitoring for token activity"""
    try:
        # Initial data fetch
        trades, holders = await process_bitquery_data(token_mint)
        for trade in trades:
            st.session_state.token_trades.append(trade)
        
        st.session_state.bitquery_last_update = datetime.now()
        
        # Polling loop
        while st.session_state.tracked_token == token_mint and not st.session_state.stop_monitoring:
            # Wait for 30 seconds before polling again
            await asyncio.sleep(30)
            
            # Skip if stopped during sleep
            if st.session_state.stop_monitoring:
                break
                
            # Poll for new data
            new_trades, new_holders = await process_bitquery_data(token_mint)
            
            # Add only new trades based on transaction hash
            existing_hashes = [t['tx_hash'] for t in st.session_state.token_trades]
            for trade in new_trades:
                if trade['tx_hash'] not in existing_hashes:
                    st.session_state.token_trades.append(trade)
                    existing_hashes.append(trade['tx_hash'])
            
            # Update timestamp
            st.session_state.bitquery_last_update = datetime.now()
            
    except Exception as e:
        st.error(f"Monitoring error: {e}")

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
        
        if st.session_state.bitquery_last_update:
            st.caption(f"Last updated: {st.session_state.bitquery_last_update.strftime('%Y-%m-%d %H:%M:%S')}")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Recent Activity")
            if st.session_state.token_trades:
                # Add filter for data sources
                data_sources = ["All Sources", "DEX Trades", "Transfers"]
                selected_source = st.selectbox("Data Source", data_sources)
                
                df_trades = pd.DataFrame(st.session_state.token_trades[-50:])
                
                # Filter by selected source
                if selected_source == "DEX Trades":
                    df_trades = df_trades[df_trades['type'] == 'dex_trade']
                elif selected_source == "Transfers":
                    df_trades = df_trades[df_trades['type'] == 'transfer']
                
                st.dataframe(df_trades.sort_values('timestamp', ascending=False), hide_index=True)
            else:
                st.info("No trades recorded yet")
        
        with col2:
            st.subheader("Token Holders")
            
            # Fetch holder data again using Bitquery
            try:
                _, holders = run_async(process_bitquery_data(st.session_state.tracked_token))
                
                if holders:
                    st.metric("Total Holders", len(holders))
                    
                    df_holders = pd.DataFrame(holders[:10])
                    df_holders['Wallet'] = df_holders['address'].apply(
                        lambda x: f"{x[:4]}...{x[-4:]}"
                    )
                    
                    df_holders['Formatted Amount'] = df_holders['amount'].apply(
                        lambda x: f"{x:,.2f}" if x >= 1 else f"{x:,.6f}"
                    )
                    
                    st.dataframe(
                        df_holders[['Wallet', 'Formatted Amount']],
                        hide_index=True,
                        use_container_width=True
                    )
                    
                    if len(holders) > 10:
                        st.download_button(
                            label="Download All Holders",
                            data=pd.DataFrame(holders).to_csv(index=False).encode('utf-8'),
                            file_name=f"{st.session_state.tracked_token[:5]}_holders.csv",
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
                format_func=lambda x: f"{x[:6]}...{x[-4:]}"
            )
            
            if selected_wallet:
                try:
                    transactions = run_async(get_wallet_transactions(selected_wallet))
                    st.session_state.wallet_history[selected_wallet] = transactions
                    
                    st.subheader(f"Wallet: {selected_wallet[:6]}...{selected_wallet[-4:]}")
                    
                    if transactions:
                        st.write(f"Recent Transactions ({len(transactions)}):")
                        df_txs = pd.DataFrame(transactions)
                        st.dataframe(df_txs, hide_index=True)
                    else:
                        st.info("No transactions found for this wallet")
                    
                    # Filter trades for this wallet
                    wallet_trades = [t for t in st.session_state.token_trades if t.get('wallet') == selected_wallet]
                    if wallet_trades:
                        st.subheader("Token-Specific Activity")
                        df_wallet_trades = pd.DataFrame(wallet_trades)
                        st.dataframe(df_wallet_trades.sort_values('timestamp', ascending=False), hide_index=True)
                except Exception as e:
                    st.error(f"Error fetching wallet history: {e}")
        else:
            st.info("No wallets detected yet. Waiting for activity...")
    else:
        st.info("Track a token to see wallet activity")

# Transaction History Tab
with tab3:
    # Create the history placeholder
    history_placeholder = st.empty()
    
    if st.session_state.tracked_token:
        st.header("Transaction History")
        
        if st.session_state.token_trades:
            df_all_trades = pd.DataFrame(st.session_state.token_trades)
            
            col1, col2, col3 = st.columns(3)
            with col1:
                min_value = st.number_input("Minimum Value (USD)", min_value=0.0, value=1.0)
            with col2:
                show_buys = st.checkbox("Show Buys", value=True)
                show_sells = st.checkbox("Show Sells", value=True)
            with col3:
                show_transfers = st.checkbox("Show Transfers", value=True)
            
            # Apply filters
            filtered = df_all_trades[df_all_trades['value'] >= min_value]
            
            filters = []
            if show_buys:
                filters.append((filtered['is_buy'] == True) & (filtered['type'] != 'transfer'))
            if show_sells:
                filters.append((filtered['is_buy'] == False) & (filtered['type'] != 'transfer'))
            if show_transfers:
                filters.append(filtered['type'] == 'transfer')
            
            if filters:
                filtered = filtered[pd.concat(filters, axis=1).any(axis=1)]
            else:
                filtered = pd.DataFrame()  # Empty if no filters selected
            
            if not filtered.empty:
                st.dataframe(filtered.sort_values('timestamp', ascending=False), hide_index=True)
                
                st.subheader("Trade Statistics")
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Transactions", len(filtered))
                
                # Calculate trade volume stats
                buy_volume = filtered[(filtered['is_buy'] == True) & (filtered['type'] != 'transfer')]['value'].sum()
                sell_volume = filtered[(filtered['is_buy'] == False) & (filtered['type'] != 'transfer')]['value'].sum()
                
                col2.metric("Buy Volume", f"${buy_volume:,.2f}")
                col3.metric("Sell Volume", f"${sell_volume:,.2f}")
            else:
                st.info("No transactions match your filter criteria")
        else:
            history_placeholder.info("No trades recorded yet")
    else:
        history_placeholder.info("Track a token to see transaction history")

# Start monitoring when a token is selected
if st.session_state.tracked_token and not st.session_state.monitor_thread:
    st.session_state.monitor_thread = start_monitoring_thread(st.session_state.tracked_token)
