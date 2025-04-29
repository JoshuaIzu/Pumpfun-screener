import asyncio
import streamlit as st
import pandas as pd
import threading
import aiohttp
from datetime import datetime, timedelta

# Configuration
BITQUERY_API_KEY = "p8HQx2XC5WVzK2dQATvUSjvGY4"  # Keep as backup
BITQUERY_ACCESS_TOKEN = "ory_at_J8RO-utFAeWyhZsPagkoV1yZZUD-GGX_ZQMqEeb9Q6Q.VRWDqmXZFzXzPeZox_865Jxo5m2b3HzGtasWrMacpOQ"
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

# Async Functions
async def get_bitquery_token_activity(token_mint: str):
    """Get token activity data from Bitquery GraphQL API"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BITQUERY_ACCESS_TOKEN}"
    }
    
    now = datetime.utcnow()
    twenty_four_hours_ago = now - timedelta(hours=24)
    
    query = """
    query TokenActivity($token: String!, $since: ISO8601DateTime) {
      Solana {
        Transfers(
          where: {Transfer: {Currency: {SmartContract: {is: $token}}, Block: {Timestamp: {after: $since}}}
          limit: {count: 50}
          orderBy: {descending: Block_Timestamp}
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
              Height
            }
            Transaction {
              Hash
            }
          }
        }
        DEXTrades(
          where: {Trade: {Buy: {Currency: {SmartContract: {is: $token}}}, Block: {Timestamp: {after: $since}}}
          limit: {count: 50}
          orderBy: {descending: Block_Timestamp}
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
              AmountInUSD: AmountInUsd
            }
            Sell {
              Currency {
                Symbol
                Name
                SmartContract
              }
              Amount
              Price
            }
            Transaction {
              Hash
            }
            Block {
              Timestamp
              Height
            }
            TradeIndex
          }
        }
        TokenHolders: BalanceUpdates(
          where: {BalanceUpdate: {Currency: {SmartContract: {is: $token}}}
          orderBy: {descendingByField: "amount"}
          limit: {count: 100}
        ) {
          BalanceUpdate {
            Address
            Amount
            AmountInUSD: AmountInUsd
          }
        }
      }
    }
    """
    
    variables = {
        "token": token_mint,
        "since": twenty_four_hours_ago.isoformat() + "Z"
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
                    error_text = await response.text()
                    st.error(f"Bitquery API error: Status {response.status}")
                    st.error(f"Error details: {error_text}")
                    return None
    except Exception as e:
        st.error(f"Error fetching data from Bitquery: {e}")
        return None

async def process_bitquery_data(token_mint: str):
    """Process Bitquery data into a format compatible with our app"""
    data = await get_bitquery_token_activity(token_mint)
    if not data or "data" not in data:
        return [], [], {}
    
    trades = []
    holders = []
    token_info = {}
    
    try:
        if "Solana" in data["data"]:
            if "Transfers" in data["data"]["Solana"] and data["data"]["Solana"]["Transfers"]:
                first_transfer = data["data"]["Solana"]["Transfers"][0]["Transfer"]
                token_info = {
                    "symbol": first_transfer["Currency"]["Symbol"] or "Unknown",
                    "name": first_transfer["Currency"]["Name"] or "Unknown",
                    "contract": first_transfer["Currency"]["SmartContract"]
                }
            elif "DEXTrades" in data["data"]["Solana"] and data["data"]["Solana"]["DEXTrades"]:
                first_trade = data["data"]["Solana"]["DEXTrades"][0]["Trade"]
                token_info = {
                    "symbol": first_trade["Buy"]["Currency"]["Symbol"] or "Unknown",
                    "name": first_trade["Buy"]["Currency"]["Name"] or "Unknown",
                    "contract": first_trade["Buy"]["Currency"]["SmartContract"]
                }
    except Exception as e:
        st.error(f"Error extracting token info: {e}")
    
    if "Solana" in data["data"] and "Transfers" in data["data"]["Solana"]:
        for transfer in data["data"]["Solana"]["Transfers"]:
            t = transfer["Transfer"]
            try:
                trade_data = {
                    'timestamp': datetime.fromisoformat(t["Block"]["Timestamp"].replace("Z", "")).strftime("%Y-%m-%d %H:%M:%S"),
                    'tx_hash': t["Transaction"]["Hash"],
                    'wallet': t["Sender"]["Address"],
                    'receiver': t["Receiver"]["Address"],
                    'amount': float(t["Amount"]),
                    'price': 0,
                    'value': float(t.get("AmountInUSD", 0)),
                    'is_buy': False,
                    'type': 'transfer',
                    'block_height': t["Block"]["Height"]
                }
                trades.append(trade_data)
                
                if t["Sender"]["Address"] not in st.session_state.tracked_wallets:
                    st.session_state.tracked_wallets.append(t["Sender"]["Address"])
                if t["Receiver"]["Address"] not in st.session_state.tracked_wallets:
                    st.session_state.tracked_wallets.append(t["Receiver"]["Address"])
            except Exception as e:
                st.error(f"Error processing transfer: {e}")
    
    if "Solana" in data["data"] and "DEXTrades" in data["data"]["Solana"]:
        for trade in data["data"]["Solana"]["DEXTrades"]:
            t = trade["Trade"]
            try:
                is_buy = t["Buy"]["Currency"]["SmartContract"] == token_mint
                trade_data = {
                    'timestamp': datetime.fromisoformat(t["Block"]["Timestamp"].replace("Z", "")).strftime("%Y-%m-%d %H:%M:%S"),
                    'tx_hash': t["Transaction"]["Hash"],
                    'wallet': "DEX Trade",
                    'amount': float(t["Buy"]["Amount"] if is_buy else t["Sell"]["Amount"]),
                    'price': float(t["Buy"]["Price"] if is_buy else t["Sell"]["Price"]),
                    'value': float(t["Buy"].get("AmountInUSD", 0)) if is_buy else float(float(t["Sell"]["Amount"]) * float(t["Sell"]["Price"])),
                    'is_buy': is_buy,
                    'type': 'dex_trade',
                    'dex': t["Dex"]["ProtocolName"],
                    'block_height': t["Block"]["Height"],
                    'trade_index': t.get("TradeIndex", 0)
                }
                trades.append(trade_data)
            except Exception as e:
                st.error(f"Error processing trade: {e}")
    
    if "Solana" in data["data"] and "TokenHolders" in data["data"]["Solana"]:
        for holder in data["data"]["Solana"]["TokenHolders"]:
            h = holder["BalanceUpdate"]
            try:
                holder_data = {
                    'address': h["Address"],
                    'amount': float(h["Amount"]),
                    'value_usd': float(h.get("AmountInUSD", 0))
                }
                holders.append(holder_data)
            except Exception as e:
                st.error(f"Error processing holder: {e}")
    
    return trades, holders, token_info

async def get_wallet_transactions(wallet_address: str):
    """Get transaction history for a wallet using Bitquery"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BITQUERY_ACCESS_TOKEN}"
    }
    
    now = datetime.utcnow()
    twenty_four_hours_ago = now - timedelta(hours=24)
    
    query = """
    query WalletActivity($address: String!, $since: ISO8601DateTime) {
      Solana {
        Transfers(
          where: {Transfer: {Sender: {Address: {is: $address}}, Block: {Timestamp: {after: $since}}}
          limit: {count: 20}
          orderBy: {descending: Block_Timestamp}
        ) {
          Transfer {
            Currency {
              Symbol
              Name
              SmartContract
            }
            Amount
            AmountInUSD: AmountInUsd
            Receiver {
              Address
            }
            Block {
              Timestamp
              Height
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
        "address": wallet_address,
        "since": twenty_four_hours_ago.isoformat() + "Z"
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
                            try:
                                tx = {
                                    'hash': t["Transaction"]["Hash"],
                                    'timestamp': datetime.fromisoformat(t["Block"]["Timestamp"].replace("Z", "")).strftime("%Y-%m-%d %H:%M:%S"),
                                    'currency': t["Currency"]["Symbol"] or t["Currency"]["SmartContract"][:10],
                                    'amount': float(t["Amount"]),
                                    'receiver': t["Receiver"]["Address"],
                                    'value_usd': float(t.get("AmountInUSD", 0)),
                                    'block_height': t["Block"]["Height"]
                                }
                                transactions.append(tx)
                            except Exception as e:
                                st.error(f"Error processing wallet transaction: {e}")
                    
                    return transactions
                else:
                    error_text = await response.text()
                    st.error(f"Wallet query error: {response.status} - {error_text}")
                    return []
    except Exception as e:
        st.error(f"Error fetching wallet data: {e}")
        return []

async def monitor_token(token_mint: str):
    """Polling-based monitoring for token activity"""
    try:
        trades, holders, token_info = await process_bitquery_data(token_mint)
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
                
            new_trades, new_holders, new_token_info = await process_bitquery_data(token_mint)
            
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
                data_sources = ["All Sources", "DEX Trades", "Transfers"]
                selected_source = st.selectbox("Data Source", data_sources)
                
                df_trades = pd.DataFrame(st.session_state.token_trades[-50:])
                
                if selected_source == "DEX Trades":
                    df_trades = df_trades[df_trades['type'] == 'dex_trade']
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
                            "type": "Type",
                            "dex": "DEX"
                        }
                    )
            else:
                st.info("No trades recorded yet")
        
        with col2:
            st.subheader("Token Holders")
            
            try:
                _, holders, _ = run_async(process_bitquery_data(st.session_state.tracked_token))
                
                if holders:
                    st.metric("Total Holders", len(holders))
                    
                    df_holders = pd.DataFrame(holders[:10])
                    df_holders['Wallet'] = df_holders['address'].apply(
                        lambda x: f"{x[:4]}...{x[-4:]}"
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
                min_value = st.number_input("Minimum Value (USD)", min_value=0.0, value=1.0, step=1.0)
            with col2:
                show_buys = st.checkbox("Show Buys", value=True)
                show_sells = st.checkbox("Show Sells", value=True)
            with col3:
                show_transfers = st.checkbox("Show Transfers", value=True)
            
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
                        "dex": "DEX",
                        "block_height": "Block"
                    }
                )
                
                st.subheader("Trade Statistics")
                col1, col2, col3 = st.columns(3)
                col1.metric("Total Transactions", len(filtered))
                
                buy_volume = filtered[(filtered['is_buy'] == True) & (filtered['type'] != 'transfer')]['value'].str.replace('$', '').str.replace(',', '').astype(float).sum()
                sell_volume = filtered[(filtered['is_buy'] == False) & (filtered['type'] != 'transfer')]['value'].str.replace('$', '').str.replace(',', '').astype(float).sum()
                
                col2.metric("Buy Volume", f"${buy_volume:,.2f}")
                col3.metric("Sell Volume", f"${sell_volume:,.2f}")
                
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
