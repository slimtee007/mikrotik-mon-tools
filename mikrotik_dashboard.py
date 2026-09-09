#!/usr/bin/env python3
"""
MikroTik Hotspot/Billing Dashboard
==================================
A responsive, immersive dashboard for monitoring MikroTik routers
with UserManager, hotspot, and billing metrics.

Requirements:
    pip install streamlit plotly pandas routeros_api psutil

Usage:
    streamlit run mikrotik_dashboard.py
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import time
import threading
from datetime import datetime, timedelta
from collections import deque
import json
import requests
import os

# MikroTik API
try:
    from routeros_api import RouterOsApiPool
    from routeros_api.exceptions import RouterOsApiConnectionError
except ImportError:
    RouterOsApiPool = None

# ==================== CONFIGURATION ====================
DEFAULT_CONFIG = {
    "host": "192.168.88.1",
    "username": "admin",
    "password": "",
    "port": 8728,
    "use_ssl": False,
    "refresh_interval": 5,
    "history_length": 60,
    "billing_provider": "None",
    "billing_key": ""
}

CONFIG_FILE = "mikrotik_config.json"

# ==================== THEME & STYLING ====================
def apply_dark_theme():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

    :root {
        --bg-primary: #0e1117;
        --bg-secondary: #161b22;
        --bg-card: #161b22;
        --accent-primary: #2f81f7;
        --accent-green: #238636;
        --accent-orange: #d29922;
        --accent-red: #da3633;
        --text-primary: #e6edf3;
        --text-secondary: #848d97;
        --border-color: #30363d;
    }

    .stApp {
        background-color: var(--bg-primary);
        font-family: 'Inter', -apple-system, sans-serif;
    }

    /* Clean cards, no glassmorphism */
    .metric-card {
        background-color: var(--bg-card);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        padding: 16px 20px;
        margin: 8px 0;
        transition: border-color 0.2s ease;
        box-shadow: 0 1px 3px rgba(0,0,0,0.12);
    }

    .metric-card:hover {
        border-color: #8b949e;
    }

    .metric-title {
        color: var(--text-secondary);
        font-size: 0.85rem;
        font-weight: 500;
        margin-bottom: 8px;
    }

    .metric-value {
        font-size: 1.75rem;
        font-weight: 600;
        color: var(--text-primary);
        margin-bottom: 4px;
        letter-spacing: -0.5px;
    }

    .metric-subtitle {
        font-size: 0.75rem;
        color: var(--text-secondary);
    }

    /* Status indicators */
    .status-online {
        display: inline-block;
        width: 8px;
        height: 8px;
        background-color: var(--accent-green);
        border-radius: 50%;
        box-shadow: 0 0 8px var(--accent-green);
    }

    .status-offline {
        display: inline-block;
        width: 8px;
        height: 8px;
        background-color: var(--accent-orange);
        border-radius: 50%;
    }

    .section-header {
        font-size: 1.25rem;
        font-weight: 600;
        color: var(--text-primary);
        margin: 24px 0 16px 0;
        padding-bottom: 8px;
        border-bottom: 1px solid var(--border-color);
    }

    /* Modernize logs */
    .log-container {
        max-height: 400px;
        overflow-y: auto;
        font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
        font-size: 0.8rem;
        background-color: var(--bg-card);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        padding: 12px;
    }
    .log-entry { margin-bottom: 6px; padding: 4px 8px; border-radius: 4px; }
    .log-info { color: var(--text-primary); }
    .log-warning { color: var(--accent-orange); }
    .log-success { color: var(--accent-green); }
    
    /* Make header transparent instead of hiding so sidebar toggle works */
    header { background: transparent !important; }
    </style>
    """, unsafe_allow_html=True)


# ==================== DATA STORE ====================
class MetricsStore:
    def __init__(self, max_len=60):
        self.max_len = max_len
        self.timestamps = deque(maxlen=max_len)
        self.cpu_history = deque(maxlen=max_len)
        self.ram_history = deque(maxlen=max_len)
        self.tx_history = deque(maxlen=max_len)
        self.rx_history = deque(maxlen=max_len)
        self.power_history = deque(maxlen=max_len)
        self.active_users_history = deque(maxlen=max_len)
        self.logs = deque(maxlen=100)
        self.last_update = None
        self.connected = False

    def add_metric(self, timestamp, cpu, ram, tx, rx, power, active_users):
        self.timestamps.append(timestamp)
        self.cpu_history.append(cpu)
        self.ram_history.append(ram)
        self.tx_history.append(tx)
        self.rx_history.append(rx)
        self.power_history.append(power)
        self.active_users_history.append(active_users)
        self.last_update = timestamp

    def add_log(self, level, message):
        self.logs.append({
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message
        })


# ==================== MIKROTIK API ====================
class MikroTikAPI:
    def __init__(self, host, username, password, port=8728, use_ssl=False):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self.api = None
        self.connection = None

    def connect(self):
        if RouterOsApiPool is None:
            return False
        try:
            self.connection = RouterOsApiPool(
                self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                use_ssl=self.use_ssl,
                plaintext_login=True
            )
            self.api = self.connection.get_api()
            return True
        except Exception as e:
            st.error(f"Connection failed: {e}")
            return False

    def disconnect(self):
        if self.connection:
            try:
                self.connection.disconnect()
            except:
                pass

    def get_system_resources(self):
        """Get CPU, RAM, and uptime info"""
        try:
            res = self.api.get_resource('/system/resource')
            data = res.get()
            if not data:
                return None
            data = data[0]
            
            # Use safe int parsing for fields that might be missing or strings
            def safe_int(val):
                try:
                    return int(val)
                except (ValueError, TypeError):
                    return 0
                    
            return {
                'cpu_load': safe_int(data.get('cpu-load', 0)),
                'free_memory': safe_int(data.get('free-memory', 0)),
                'total_memory': safe_int(data.get('total-memory', 0)),
                'uptime': data.get('uptime', 'N/A'),
                'version': data.get('version', 'N/A'),
                'board_name': data.get('board-name', 'N/A'),
                'architecture': data.get('architecture-name', 'N/A')
            }
        except Exception as e:
            return None

    def get_power_usage(self):
        """Get power consumption if supported"""
        result = {'voltage': 0.0, 'current': 0.0, 'power': 0.0, 'temperature': None}
        try:
            # Try health resource first (newer RouterOS)
            health = self.api.get_resource('/system/health')
            health_data = health.get()
            for item in health_data:
                if 'voltage' in item:
                    voltage = float(item.get('voltage', 0)) / 10
                    current = float(item.get('current', 0)) / 1000 if 'current' in item else 0.0
                    power = voltage * current if current > 0 else voltage * 0.5
                    result['voltage'] = voltage
                    result['current'] = current
                    result['power'] = round(power, 2)
                    if 'temperature' in item:
                        result['temperature'] = float(item.get('temperature', 0)) / 10
                    return result
            # Fallback to routerboard
            rb = self.api.get_resource('/system/routerboard')
            rb_data = rb.get()[0]
            if 'current-voltage' in rb_data:
                result['voltage'] = float(rb_data.get('current-voltage', 0)) / 10
            if 'temperature' in rb_data:
                result['temperature'] = float(rb_data.get('temperature', 0)) / 10
            return result
        except Exception:
            return result

    def get_interface_stats(self):
        """Get network interface statistics"""
        try:
            interfaces = self.api.get_resource('/interface')
            stats = interfaces.get()

            total_rx = 0
            total_tx = 0
            interface_data = []

            for iface in stats:
                if iface.get('type') == 'ether' or 'wlan' in iface.get('type', ''):
                    rx = int(iface.get('rx-byte', 0))
                    tx = int(iface.get('tx-byte', 0))
                    total_rx += rx
                    total_tx += tx

                    interface_data.append({
                        'name': iface.get('name'),
                        'type': iface.get('type'),
                        'rx': self._format_bytes(rx),
                        'tx': self._format_bytes(tx),
                        'rx_raw': rx,
                        'tx_raw': tx,
                        'status': 'up' if iface.get('running') == 'true' else 'down'
                    })

            return {
                'total_rx': total_rx,
                'total_tx': total_tx,
                'interfaces': interface_data
            }
        except Exception as e:
            return None

    def get_active_hotspot_users(self):
        """Get currently active hotspot users"""
        try:
            users = self.api.get_resource('/ip/hotspot/active')
            active = users.get()
            return [{
                'user': u.get('user', 'N/A'),
                'address': u.get('address', 'N/A'),
                'mac': u.get('mac-address', 'N/A'),
                'uptime': u.get('uptime', 'N/A'),
                'bytes_in': self._format_bytes(int(u.get('bytes-in', 0))),
                'bytes_out': self._format_bytes(int(u.get('bytes-out', 0)))
            } for u in active]
        except Exception:
            return []


    def _get_um_data(self, path):
        """Helper to fetch data from UM, falling back from v6 to v7 path"""
        try:
            res = self.api.get_resource(f'/tool/user-manager/{path}')
            return res.get(), res
        except Exception:
            try:
                res = self.api.get_resource(f'/user-manager/{path}')
                return res.get(), res
            except Exception as e:
                raise e

    def generate_vouchers(self, count=1, length=6, prefix="", profile=None):
        """Generate random vouchers in UserManager"""
        import random
        import string
        try:
            # We just need the resource object to call add()
            # We can figure out the right path by doing a get() on a harmless list like 'user'
            try:
                self.api.get_resource('/tool/user-manager/user').get()
                prefix_path = '/tool/user-manager'
            except Exception:
                prefix_path = '/user-manager'
                
            user_resource = self.api.get_resource(f'{prefix_path}/user')
            up_resource = self.api.get_resource(f'{prefix_path}/user-profile')

            vouchers = []
            for _ in range(count):
                chars = string.ascii_lowercase + string.digits
                username = prefix + ''.join(random.choice(chars) for _ in range(length))
                password = ''.join(random.choice(chars) for _ in range(length))
                
                try:
                    user_resource.add(customer="admin", username=username, password=password)
                except Exception:
                    try:
                        # RouterOS v6 without customer
                        user_resource.add(username=username, password=password)
                    except Exception:
                        # RouterOS v7 uses 'name'
                        user_resource.add(name=username, password=password)
                
                if profile and profile != "None":
                    try:
                        up_resource.add(customer="admin", user=username, profile=profile)
                    except Exception:
                        try:
                            up_resource.add(user=username, profile=profile)
                        except Exception:
                            pass # Profile binding failed
                            
                vouchers.append({'username': username, 'password': password})
            return vouchers
        except Exception as e:
            return str(e)

    def get_usermanager_users(self):
        """Get UserManager users and vouchers"""
        try:
            all_users, _ = self._get_um_data('user')
            
            results = []
            for u in all_users:
                # Handle missing or empty bytes-used safely
                try:
                    b_used = int(u.get('bytes-used', 0))
                except (ValueError, TypeError):
                    b_used = 0
                    
                results.append({
                    'username': u.get('name', u.get('username', 'N/A')),
                    'shared_users': u.get('shared-users', '1'),
                    'uptime_used': u.get('uptime-used', '0s'),
                    'bytes_used': self._format_bytes(b_used),
                    'disabled': u.get('disabled', 'false') == 'true'
                })
            return results
        except Exception:
            return []

    def get_usermanager_sessions(self):
        """Get active UserManager sessions"""
        try:
            active_sessions, _ = self._get_um_data('session')
            return [{
                'user': s.get('user', 'N/A'),
                'calling_station': s.get('calling-station-id', 'N/A'),
                'uptime': s.get('uptime', 'N/A'),
                'bytes_in': self._format_bytes(int(s.get('bytes-in', 0))),
                'bytes_out': self._format_bytes(int(s.get('bytes-out', 0)))
            } for s in active_sessions]
        except Exception:
            return []

    def get_usermanager_profiles(self):
        """Get UserManager profiles"""
        try:
            all_profiles, _ = self._get_um_data('profile')
            return [{
                'name': p.get('name', 'N/A'),
                'price': p.get('price', '0'),
                'validity': p.get('validity', '0s')
            } for p in all_profiles]
        except Exception:
            return []

    def get_usermanager_user_profiles(self):
        """Get UserManager user profiles"""
        try:
            all_u_profiles, _ = self._get_um_data('user-profile')
            return [{
                'user': p.get('user', 'N/A'),
                'profile': p.get('profile', 'N/A'),
            } for p in all_u_profiles]
        except Exception:
            return []

    def get_logs(self, topics=""):
        """Get system logs"""
        try:
            logs = self.api.get_resource('/log')
            if topics:
                log_data = logs.get(topics=topics)
            else:
                log_data = logs.get()
            return [{
                'time': l.get('time', ''),
                'topics': l.get('topics', ''),
                'message': l.get('message', '')
            } for l in log_data[-50:]]  # Last 50 logs
        except Exception:
            return []

    @staticmethod
    def _format_bytes(bytes_val):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_val < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} PB"


# ==================== DEMO DATA (when no connection) ====================
class DemoData:
    """Generate realistic demo data when MikroTik is not connected"""

    _demo_um_users = [
        {'username': 'admin', 'shared_users': '1', 'uptime_used': '12h30m', 'bytes_used': '5.2 GB', 'disabled': False},
        {'username': 'voucher_1day', 'shared_users': '3', 'uptime_used': '4h15m', 'bytes_used': '1.8 GB', 'disabled': False},
        {'username': 'voucher_1week', 'shared_users': '5', 'uptime_used': '2d6h', 'bytes_used': '12.5 GB', 'disabled': False},
        {'username': 'guest_temp', 'shared_users': '1', 'uptime_used': '30m', 'bytes_used': '150 MB', 'disabled': True},
    ]

    @staticmethod
    def get_system_resources():
        import random
        return {
            'cpu_load': random.randint(15, 65),
            'free_memory': random.randint(200000000, 400000000),
            'total_memory': 536870912,
            'uptime': '3d12h45m',
            'version': '7.12.1',
            'board_name': 'hAP ac³',
            'architecture': 'arm64'
        }

    @staticmethod
    def get_power_usage():
        import random
        return {
            'voltage': 24.0 + random.uniform(-0.5, 0.5),
            'current': 0.5 + random.uniform(-0.05, 0.05),
            'power': 12.0 + random.uniform(-1, 1),
            'temperature': 45 + random.uniform(-3, 3)
        }

    @staticmethod
    def get_interface_stats():
        import random
        return {
            'total_rx': 150000000000 + random.randint(0, 1000000000),
            'total_tx': 80000000000 + random.randint(0, 500000000),
            'interfaces': [
                {'name': 'ether1', 'type': 'ether', 'rx': '145.2 GB', 'tx': '78.5 GB', 'rx_raw': 145200000000, 'tx_raw': 78500000000, 'status': 'up'},
                {'name': 'ether2', 'type': 'ether', 'rx': '2.1 GB', 'tx': '5.3 GB', 'rx_raw': 2100000000, 'tx_raw': 5300000000, 'status': 'up'},
                {'name': 'wlan1', 'type': 'wlan', 'rx': '12.5 GB', 'tx': '8.2 GB', 'rx_raw': 12500000000, 'tx_raw': 8200000000, 'status': 'up'},
                {'name': 'wlan2', 'type': 'wlan', 'rx': '0 B', 'tx': '0 B', 'rx_raw': 0, 'tx_raw': 0, 'status': 'down'},
            ]
        }

    @staticmethod
    def get_active_hotspot_users():
        return [
            {'user': 'guest001', 'address': '192.168.88.245', 'mac': 'AA:BB:CC:11:22:33', 'uptime': '2h15m', 'bytes_in': '1.2 GB', 'bytes_out': '450 MB'},
            {'user': 'guest002', 'address': '192.168.88.246', 'mac': 'DD:EE:FF:44:55:66', 'uptime': '45m', 'bytes_in': '350 MB', 'bytes_out': '120 MB'},
            {'user': 'voucher_abc123', 'address': '192.168.88.247', 'mac': '11:22:33:77:88:99', 'uptime': '15m', 'bytes_in': '85 MB', 'bytes_out': '30 MB'},
        ]


    @classmethod
    def generate_vouchers(cls, count=1, length=6, prefix="", profile=None):
        import random
        import string
        import time
        vouchers = []
        for _ in range(count):
            chars = string.ascii_lowercase + string.digits
            username = prefix + ''.join(random.choice(chars) for _ in range(length))
            password = ''.join(random.choice(chars) for _ in range(length))
            vouchers.append({'username': username, 'password': password})
            cls._demo_um_users.append({
                'username': username, 'shared_users': '1', 'uptime_used': '0s', 'bytes_used': '0 B', 'disabled': False
            })
        time.sleep(0.5)
        return vouchers

    @classmethod
    def get_usermanager_users(cls):
        return cls._demo_um_users

    @staticmethod
    def get_usermanager_sessions():
        return [
            {'user': 'guest001', 'calling_station': 'AA:BB:CC:11:22:33', 'uptime': '2h15m', 'bytes_in': '1.2 GB', 'bytes_out': '450 MB'},
            {'user': 'guest002', 'calling_station': 'DD:EE:FF:44:55:66', 'uptime': '45m', 'bytes_in': '350 MB', 'bytes_out': '120 MB'},
        ]


    @staticmethod
    def get_usermanager_profiles():
        return [
            {'name': '1-Hour', 'price': '1.00', 'validity': '1h'},
            {'name': '1-Day', 'price': '10.00', 'validity': '1d'},
            {'name': '1-Week', 'price': '50.00', 'validity': '1w'}
        ]

    @staticmethod
    def get_usermanager_user_profiles():
        return [
            {'user': 'voucher_1day', 'profile': '1-Day'},
            {'user': 'voucher_1week', 'profile': '1-Week'}
        ]

    @staticmethod
    def get_logs():
        return [
            {'time': '15:42:15', 'topics': 'system,info', 'message': 'user admin logged in from 192.168.88.10 via winbox'},
            {'time': '15:40:22', 'topics': 'hotspot,info', 'message': 'guest001 (192.168.88.245): logged in'},
            {'time': '15:38:10', 'topics': 'dhcp,info', 'message': 'assigned 192.168.88.247 to 11:22:33:77:88:99'},
            {'time': '15:35:45', 'topics': 'system,warning', 'message': 'cpu usage exceeded 80%'},
            {'time': '15:30:00', 'topics': 'firewall,info', 'message': 'drop input from WAN'},
        ]


# ==================== CHART COMPONENTS ====================
def create_gauge_chart(value, title, color, suffix="%"):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        title={'text': title, 'font': {'size': 14, 'color': '#94a3b8'}},
        number={'suffix': suffix, 'font': {'size': 24, 'color': '#e2e8f0'}},
        gauge={
            'axis': {'range': [0, 100], 'tickwidth': 1, 'tickcolor': '#2d3748'},
            'bar': {'color': color},
            'bgcolor': 'rgba(0,0,0,0)',
            'borderwidth': 2,
            'bordercolor': '#2d3748',
            'steps': [
                {'range': [0, 50], 'color': 'rgba(0, 230, 118, 0.1)'},
                {'range': [50, 80], 'color': 'rgba(255, 145, 0, 0.1)'},
                {'range': [80, 100], 'color': 'rgba(255, 82, 82, 0.1)'}
            ],
            'threshold': {
                'line': {'color': color, 'width': 4},
                'thickness': 0.75,
                'value': value
            }
        }
    ))
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        height=200,
        margin=dict(l=20, r=20, t=40, b=20),
        font={'family': 'Inter'}
    )
    return fig


def create_time_series(timestamps, data_dict, title, colors):
    fig = go.Figure()
    for label, data in data_dict.items():
        fig.add_trace(go.Scatter(
            x=list(timestamps),
            y=list(data),
            mode='lines',
            name=label,
            line=dict(color=colors.get(label, '#2f81f7'), width=2),
            fill='tozeroy',
            fillcolor=f"rgba{tuple(list(int(colors.get(label, '#2f81f7')[i:i+2], 16) for i in (1, 3, 5)) + [0.1])}"
        ))
    fig.update_layout(
        title={'text': title, 'font': {'color': '#e2e8f0', 'size': 16}},
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font={'color': '#94a3b8', 'family': 'Inter'},
        xaxis=dict(gridcolor='#30363d', showgrid=True),
        yaxis=dict(gridcolor='#30363d', showgrid=True),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        margin=dict(l=40, r=20, t=60, b=40),
        height=300
    )
    return fig


def create_network_usage_chart(interfaces):
    names = [i['name'] for i in interfaces]
    rx_vals = [i['rx_raw'] / (1024**3) for i in interfaces]  # GB
    tx_vals = [i['tx_raw'] / (1024**3) for i in interfaces]

    fig = go.Figure(data=[
        go.Bar(name='RX (Download)', x=names, y=rx_vals, marker_color='#2f81f7'),
        go.Bar(name='TX (Upload)', x=names, y=tx_vals, marker_color='#8957e5')
    ])
    fig.update_layout(
        barmode='group',
        title={'text': 'Interface Traffic (GB)', 'font': {'color': '#e2e8f0', 'size': 16}},
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font={'color': '#94a3b8', 'family': 'Inter'},
        xaxis=dict(gridcolor='#30363d'),
        yaxis=dict(gridcolor='#30363d', title='GB'),
        legend=dict(orientation='h', yanchor='bottom', y=1.02),
        margin=dict(l=40, r=20, t=60, b=40),
        height=300
    )
    return fig


def create_user_pie_chart(active_count, total_count):
    fig = go.Figure(data=[go.Pie(
        labels=['Active', 'Inactive'],
        values=[active_count, max(0, total_count - active_count)],
        hole=0.6,
        marker_colors=['#238636', '#2d3748'],
        textinfo='none'
    )])
    fig.update_layout(
        annotations=[dict(text=f'{active_count}', x=0.5, y=0.5, font_size=28, showarrow=False, font_color='#e2e8f0')],
        paper_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
        margin=dict(l=20, r=20, t=20, b=20),
        height=200
    )
    return fig


# ==================== MAIN DASHBOARD ====================
def render_metric_card(title, value, subtitle, color_class="", icon=""):
    st.markdown(f"""
    <div class="metric-card animate-in">
        <div class="metric-title">{icon} {title}</div>
        <div class="metric-value" style="color: {color_class};">{value}</div>
        <div class="metric-sub">{subtitle}</div>
    </div>
    """, unsafe_allow_html=True)


def main():
    st.set_page_config(
        page_title="MikroTik Dashboard",
        page_icon="📡",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    apply_dark_theme()

    # Initialize session state
    if 'store' not in st.session_state:
        st.session_state.store = MetricsStore(max_len=DEFAULT_CONFIG['history_length'])
    if 'config' not in st.session_state:
        st.session_state.config = DEFAULT_CONFIG.copy()
    if 'api' not in st.session_state:
        st.session_state.api = None
    if 'demo_mode' not in st.session_state:
        st.session_state.demo_mode = True

    store = st.session_state.store

    # ==================== SIDEBAR ====================
    with st.sidebar:
        st.markdown("""
        <div style="text-align: center; padding: 20px 0;">
            <h1 style="color: #e6edf3; font-size: 1.25rem; font-weight: 600; margin: 0;">MikroTik Monitor</h1>
            <p style="color: #94a3b8; font-size: 0.8rem; margin: 5px 0 0 0;">Hotspot & Billing Monitor</p>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("---")

        # Connection Settings
        with st.expander("Connection Settings", expanded=True):
            host = st.text_input("Host", value=st.session_state.config['host'])
            username = st.text_input("Username", value=st.session_state.config['username'])
            password = st.text_input("Password", value=st.session_state.config['password'], type="password")
            port = st.number_input("Port", value=st.session_state.config['port'], min_value=1, max_value=65535)

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Connect", use_container_width=True):
                    st.session_state.config.update({'host': host, 'username': username, 'password': password, 'port': port})
                    api = MikroTikAPI(host, username, password, port)
                    if api.connect():
                        st.session_state.api = api
                        st.session_state.demo_mode = False
                        store.add_log("success", f"Connected to {host}")
                        st.rerun()
                    else:
                        store.add_log("error", f"Failed to connect to {host}")
                        st.error("Connection failed!")
            with col2:
                if st.button("Demo", use_container_width=True):
                    st.session_state.demo_mode = True
                    st.session_state.api = None
                    store.add_log("info", "Switched to demo mode")
                    st.rerun()


        with st.expander("💳 Billing Integration (NGN)"):
            b_prov = st.session_state.config.get('billing_provider', 'None')
            billing_provider = st.selectbox("Payment Gateway", ["None", "Paystack", "Flutterwave", "PalmPay"], index=["None", "Paystack", "Flutterwave", "PalmPay"].index(b_prov) if b_prov in ["None", "Paystack", "Flutterwave", "PalmPay"] else 0)
            billing_key = st.text_input("Secret API Key", value=st.session_state.config.get('billing_key', ''), type="password")
            if st.button("Save Billing", use_container_width=True):
                st.session_state.config['billing_provider'] = billing_provider
                st.session_state.config['billing_key'] = billing_key
                st.rerun()

        # Status indicator
        if st.session_state.demo_mode:
            st.markdown("""
            <div style="display: flex; align-items: center; gap: 8px; padding: 10px; background: rgba(255, 145, 0, 0.1); border-radius: 8px;">
                <span class="status-offline"></span>
                <span style="color: #d29922; font-size: 0.9rem;">Demo Mode</span>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="display: flex; align-items: center; gap: 8px; padding: 10px; background: rgba(0, 230, 118, 0.1); border-radius: 8px;">
                <span class="status-online"></span>
                <span style="color: #238636; font-size: 0.9rem;">Connected</span>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")

        # Refresh settings
        refresh = st.slider("Refresh (sec)", 1, 30, st.session_state.config['refresh_interval'])
        st.session_state.config['refresh_interval'] = refresh

        # Auto refresh
        auto_refresh = st.toggle("Auto Refresh", value=True)

        st.markdown("---")
        st.markdown("""
        <div style="text-align: center; color: #64748b; font-size: 0.75rem;">
            MikroTik Dashboard v2.0<br>
            Built with Streamlit
        </div>
        """, unsafe_allow_html=True)

    # ==================== HEADER ====================
    st.markdown("""
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
        <div>
            <h1 style="color: #e2e8f0; margin: 0; font-size: 1.8rem;">Network Operations Center</h1>
            <p style="color: #94a3b8; margin: 5px 0 0 0; font-size: 0.9rem;">Real-time MikroTik monitoring & billing analytics</p>
        </div>
        <div style="text-align: right;">
            <p style="color: #64748b; margin: 0; font-size: 0.8rem;">Last Update</p>
            <p style="color: #2f81f7; margin: 0; font-size: 1rem; font-weight: 600;">{}</p>
        </div>
    </div>
    """.format(store.last_update.strftime("%H:%M:%S") if store.last_update else "--:--:--"), unsafe_allow_html=True)

    # ==================== FETCH DATA ====================
    api = st.session_state.api
    demo = st.session_state.demo_mode

    now = time.time()
    if 'data_cache' not in st.session_state:
        st.session_state.data_cache = {}

    def fetch_with_cache(key, fetch_func, ttl=10):
        if key not in st.session_state.data_cache or (now - st.session_state.data_cache[key]['time']) > ttl:
            st.session_state.data_cache[key] = {'data': fetch_func(), 'time': now}
        return st.session_state.data_cache[key]['data']

    if demo or api is None:
        resources = DemoData.get_system_resources()
        power = DemoData.get_power_usage()
        interfaces = DemoData.get_interface_stats()
        hotspot_users = DemoData.get_active_hotspot_users()
        um_users = fetch_with_cache("demo_um_users", DemoData.get_usermanager_users, ttl=30)
        um_sessions = DemoData.get_usermanager_sessions()
        um_profiles = fetch_with_cache("demo_um_profiles", DemoData.get_usermanager_profiles, ttl=30)
        um_user_profiles = fetch_with_cache("demo_um_user_profiles", DemoData.get_usermanager_user_profiles, ttl=30)
        logs = fetch_with_cache("demo_logs", DemoData.get_logs, ttl=10)
    else:
        resources = api.get_system_resources()
        power = api.get_power_usage()
        interfaces = api.get_interface_stats()
        hotspot_users = api.get_active_hotspot_users()
        # Cache UserManager users for 30s as this list can be huge and slow to fetch
        um_users = fetch_with_cache("api_um_users", api.get_usermanager_users, ttl=30)
        um_sessions = api.get_usermanager_sessions()
        um_profiles = fetch_with_cache("api_um_profiles", api.get_usermanager_profiles, ttl=30)
        um_user_profiles = fetch_with_cache("api_um_user_profiles", api.get_usermanager_user_profiles, ttl=30)
        # Cache logs for 10s to reduce router load
        logs = fetch_with_cache("api_logs", api.get_logs, ttl=10)

    if resources and resources.get('total_memory', 0) > 0:
        ram_used = ((resources['total_memory'] - resources['free_memory']) / resources['total_memory']) * 100
        store.add_metric(
            datetime.now(),
            resources['cpu_load'],
            ram_used,
            interfaces['total_tx'] if interfaces else 0,
            interfaces['total_rx'] if interfaces else 0,
            power['power'] if power else 0,
            len(hotspot_users) if hotspot_users else 0
        )
    else:
        ram_used = 0

    # ==================== TOP METRICS ROW ====================
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        render_metric_card(
            "CPU Load",
            f"{resources['cpu_load']}%" if resources else "N/A",
            f"Architecture: {resources.get('architecture', 'N/A')}" if resources else "",
            "#2f81f7",
            "🖥️"
        )

    with col2:
        ram_pct = round(ram_used, 1) if resources else 0
        render_metric_card(
            "RAM Usage",
            f"{ram_pct}%" if resources else "N/A",
            f"Free: {MikroTikAPI._format_bytes(resources['free_memory']) if resources else 'N/A'}",
            "#8957e5",
            "💾"
        )

    with col3:
        power_val = power.get('power', 0) if power else 0
        voltage_val = power.get('voltage', 0) if power else 0
        current_val = power.get('current', 0) if power else 0
        render_metric_card(
            "Power Draw",
            f"{power_val:.1f}W" if power else "N/A",
            f"{voltage_val:.1f}V @ {current_val:.2f}A" if power else "",
            "#238636",
            "⚡"
        )

    with col4:
        temp = power.get('temperature') if power else None
        temp_display = f"{temp:.1f}°C" if isinstance(temp, (int, float)) else "N/A"
        temp_color = "#d29922" if isinstance(temp, (int, float)) and temp > 60 else "#238636"
        render_metric_card(
            "Temperature",
            temp_display,
            "System thermal status",
            temp_color,
            "🌡️"
        )

    with col5:
        uptime = resources.get('uptime', 'N/A') if resources else 'N/A'
        render_metric_card(
            "Uptime",
            uptime,
            f"RouterOS {resources.get('version', 'N/A')}" if resources else "",
            "#e2e8f0",
            "⏱️"
        )


    # ==================== CHARTS ROW 1 ====================
    st.markdown('<div class="section-header">System Performance</div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        if resources is not None:
            fig = create_gauge_chart(resources.get('cpu_load', 0), "CPU", "#2f81f7")
            st.plotly_chart(fig, use_container_width=True, key="cpu_gauge")

    with col2:
        if resources is not None:
            # We already have ram_used calculated safely above
            fig = create_gauge_chart(ram_used, "RAM", "#8957e5")
            st.plotly_chart(fig, use_container_width=True, key="ram_gauge")

    with col3:
        if power and isinstance(power.get('temperature'), (int, float)):
            temp_val = min(power['temperature'], 100)
            fig = create_gauge_chart(temp_val, "Temp", "#d29922", "°C")
            st.plotly_chart(fig, use_container_width=True, key="temp_gauge")
        else:
            fig = create_gauge_chart(0, "Temp", "#64748b", "°C")
            st.plotly_chart(fig, use_container_width=True, key="temp_gauge_na")
    # ==================== CHARTS ROW 2 ====================
    col1, col2 = st.columns(2)

    with col1:
        if len(store.timestamps) > 1:
            fig = create_time_series(
                store.timestamps,
                {"CPU": store.cpu_history, "RAM": store.ram_history},
                "CPU & RAM History",
                {"CPU": "#2f81f7", "RAM": "#8957e5"}
            )
            st.plotly_chart(fig, use_container_width=True, key="sys_history")

    with col2:
        if len(store.timestamps) > 1:
            fig = create_time_series(
                store.timestamps,
                {"Active Users": store.active_users_history},
                "Active Hotspot Users",
                {"Active Users": "#238636"}
            )
            st.plotly_chart(fig, use_container_width=True, key="users_history")

    # ==================== NETWORK SECTION ====================
    st.markdown('<div class="section-header">Network Utilization</div>', unsafe_allow_html=True)

    col1, col2 = st.columns([2, 1])

    with col1:
        if interfaces:
            fig = create_network_usage_chart(interfaces['interfaces'])
            st.plotly_chart(fig, use_container_width=True, key="net_chart")

    with col2:
        if interfaces:
            st.markdown("""
            <div class="metric-card">
                <div class="metric-title">Interface Status</div>
            </div>
            """, unsafe_allow_html=True)

            iface_df = pd.DataFrame([
                {
                    'Interface': i['name'],
                    'Status': '🟢 UP' if i['status'] == 'up' else '🔴 DOWN',
                    'RX': i['rx'],
                    'TX': i['tx']
                } for i in interfaces['interfaces']
            ])
            st.dataframe(iface_df, use_container_width=True, hide_index=True, height=250)

    # ==================== HOTSPOT / USERMANAGER ====================
    st.markdown('<div class="section-header">Hotspot & UserManager</div>', unsafe_allow_html=True)

    # Revenue Calculation
    today_revenue = 0.0
    b_prov = st.session_state.config.get('billing_provider', 'None')
    b_key = st.session_state.config.get('billing_key', '')
    
    def fetch_revenue():
        if b_prov == "Paystack" and b_key:
            try:
                today = datetime.now().strftime("%Y-%m-%dT00:00:00.000Z")
                res = requests.get(f"https://api.paystack.co/transaction?status=success&from={today}", 
                                 headers={"Authorization": f"Bearer {b_key}"})
                data = res.json()
                return sum(t.get('amount', 0) for t in data.get('data', [])) / 100
            except:
                return 0.0
        elif b_prov == "Flutterwave" and b_key:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                res = requests.get(f"https://api.flutterwave.com/v3/transactions?status=successful&from={today}", 
                                 headers={"Authorization": f"Bearer {b_key}"})
                data = res.json()
                return sum(t.get('amount', 0) for t in data.get('data', []))
            except:
                return 0.0
        elif b_prov == "PalmPay" and b_key:
            try:
                # Generalized PalmPay API fetch for daily successful transactions
                # (Note: Exact endpoint depends on PalmPay Merchant vs POS integration)
                today = datetime.now().strftime("%Y-%m-%d")
                res = requests.get(f"https://api.palmpay.com/v2/transactions?status=SUCCESS&date={today}", 
                                 headers={"Authorization": f"Bearer {b_key}"})
                data = res.json()
                return sum(t.get('amount', 0) for t in data.get('data', []))
            except:
                return 0.0
        return 0.0
        
    today_revenue = fetch_with_cache("daily_revenue", fetch_revenue, ttl=60)
    
    # Estimated revenue based on active UM users and profiles
    estimated_revenue = 0.0
    if um_users and um_profiles and um_user_profiles:
        try:
            prof_prices = {p['name']: float(p.get('price', 0) or 0) for p in um_profiles}
            for u in um_users:
                if not u.get('disabled', True):
                    # Find profile
                    assigned = next((up['profile'] for up in um_user_profiles if up['user'] == u['username']), None)
                    if assigned and assigned in prof_prices:
                        estimated_revenue += prof_prices[assigned]
        except:
            pass

    rev_col1, rev_col2 = st.columns(2)
    with rev_col1:
        render_metric_card(
            "Today's Live Revenue (API)", 
            f"₦ {today_revenue:,.2f}", 
            f"via {b_prov}" if b_prov != "None" else "No Gateway Connected", 
            "#238636", 
            "💳"
        )
    with rev_col2:
        render_metric_card(
            "Estimated Active Value", 
            f"₦ {estimated_revenue:,.2f}", 
            "Based on active users × profile price", 
            "#2f81f7", 
            "📈"
        )
    
    st.markdown("<br>", unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs(["Active Hotspot", "User Manager", "Active Sessions", "UM Profiles"])

    with tab1:
        if hotspot_users:
            df = pd.DataFrame(hotspot_users)
            df = df.rename(columns={
                'user': 'Username',
                'address': 'IP Address',
                'mac': 'MAC Address',
                'uptime': 'Uptime',
                'bytes_in': 'Download',
                'bytes_out': 'Upload'
            })
            
            search_hotspot = st.text_input("Search Active Hotspot Users", placeholder="Type a username...")
            if search_hotspot:
                df = df[df['Username'].str.contains(search_hotspot, case=False, na=False)]
                
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.markdown(f"""
            <div style="display: flex; gap: 20px; margin-top: 10px;">
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-title">Total Active</div>
                    <div class="metric-value" style="color: #238636;">{len(hotspot_users)}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("No active hotspot users")

    with tab2:
        st.markdown("### Voucher Generator")
        with st.form("voucher_generator"):
            col_gen1, col_gen2, col_gen3, col_gen4 = st.columns(4)
            with col_gen1:
                voucher_count = st.number_input("Number", min_value=1, max_value=500, value=5)
            with col_gen2:
                voucher_prefix = st.text_input("Prefix", value="")
            with col_gen3:
                voucher_length = st.number_input("Length", min_value=4, max_value=16, value=6)
            with col_gen4:
                prof_opts = ["None"] + [p.get('name', '') for p in um_profiles] if um_profiles else ["None"]
                voucher_profile = st.selectbox("Profile", prof_opts)
            
            generate_btn = st.form_submit_button("Generate Vouchers", use_container_width=True)
            
        if generate_btn:
            with st.spinner("Generating..."):
                if demo or api is None:
                    new_vouchers = DemoData.generate_vouchers(voucher_count, voucher_length, voucher_prefix, voucher_profile)
                else:
                    new_vouchers = api.generate_vouchers(voucher_count, voucher_length, voucher_prefix, voucher_profile)
                
                if isinstance(new_vouchers, str):
                    st.error(f"Error: {new_vouchers}")
                elif new_vouchers:
                    st.success(f"Successfully generated {len(new_vouchers)} vouchers!")
                    with st.expander("View Generated Credentials", expanded=True):
                        st.dataframe(pd.DataFrame(new_vouchers), use_container_width=True)
                    
                    if 'data_cache' in st.session_state:
                        st.session_state.data_cache.pop('api_um_users', None)
                        st.session_state.data_cache.pop('demo_um_users', None)

        st.markdown("---")
        st.markdown("### User Database")
        
        if um_users:
            df = pd.DataFrame(um_users)
            df['Status'] = df['disabled'].apply(lambda x: '🔴 Disabled' if x else '🟢 Active')
            
            # Map Profiles if they exist
            if um_user_profiles:
                up_dict = {p['user']: p['profile'] for p in um_user_profiles}
                df['Profile'] = df['username'].map(up_dict).fillna('None')
            else:
                df['Profile'] = 'None'
            
            search_um = st.text_input("Search User Manager Users", placeholder="Type a username...")
            if search_um:
                df = df[df['username'].str.contains(search_um, case=False, na=False)]
                
            display_df = df[['username', 'Profile', 'shared_users', 'uptime_used', 'bytes_used', 'Status']].rename(
                columns={
                    'username': 'Username',
                    'shared_users': 'Shared Users',
                    'uptime_used': 'Uptime Used',
                    'bytes_used': 'Data Used'
                }
            )
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            active_um = len([u for u in um_users if not u['disabled']])
            col1, col2 = st.columns(2)
            with col1:
                fig = create_user_pie_chart(active_um, len(um_users))
                st.plotly_chart(fig, use_container_width=True, key="um_pie")
            with col2:
                render_metric_card("Total Vouchers", len(um_users), f"{active_um} active", "#8957e5", "🎫")
        else:
            st.info("No UserManager users found")

    with tab3:
        if um_sessions:
            df = pd.DataFrame(um_sessions)
            df = df.rename(columns={
                'user': 'Username',
                'calling_station': 'MAC Address',
                'uptime': 'Session Uptime',
                'bytes_in': 'Download',
                'bytes_out': 'Upload'
            })
            
            search_sessions = st.text_input("Search Active Sessions", placeholder="Type a username or MAC...")
            if search_sessions:
                df = df[df.apply(lambda row: df.astype(str).apply(lambda x: search_sessions.lower() in x.lower(), axis=1)).any(axis=1)]
                
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No active UserManager sessions")

    with tab4:
        st.markdown("### Available Profiles")
        if um_profiles:
            df_profiles = pd.DataFrame(um_profiles)
            df_profiles = df_profiles.rename(columns={'name': 'Profile Name', 'price': 'Price', 'validity': 'Validity'})
            st.dataframe(df_profiles, use_container_width=True, hide_index=True)
        else:
            st.info("No profiles found")
            
        st.markdown("---")
        st.markdown("### Profile Allocation")
        if um_user_profiles:
            df_up = pd.DataFrame(um_user_profiles)
            df_up = df_up.rename(columns={'user': 'Username', 'profile': 'Assigned Profile'})
            
            search_up = st.text_input("Search Assigned Profiles", placeholder="Type a username...")
            if search_up:
                df_up = df_up[df_up['Username'].str.contains(search_up, case=False, na=False)]
                
            st.dataframe(df_up, use_container_width=True, hide_index=True)
        else:
            st.info("No user-profiles found")

    # ==================== LOGS SECTION ====================
    st.markdown('<div class="section-header">System Logs</div>', unsafe_allow_html=True)

    log_filter = st.selectbox("Filter by topic", ["All", "system", "hotspot", "dhcp", "firewall", "warning", "error"], index=0)

    if logs:
        filtered_logs = logs
        if log_filter != "All":
            filtered_logs = [l for l in logs if log_filter in l.get('topics', '')]

        for log in filtered_logs[-20:]:
            level = "info"
            if "error" in log.get('topics', ''):
                level = "error"
            elif "warning" in log.get('topics', ''):
                level = "warning"
            elif "hotspot" in log.get('topics', '') and "logged in" in log.get('message', ''):
                level = "success"

            st.markdown(f"""
            <div class="log-entry log-{level}">
                <strong>[{log.get('time', '--:--:--')}]</strong> 
                <span style="opacity: 0.7;">[{log.get('topics', '')}]</span> 
                {log.get('message', '')}
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No logs available")

    # ==================== AUTO REFRESH ====================
    if auto_refresh:
        time.sleep(st.session_state.config['refresh_interval'])
        st.rerun()


if __name__ == "__main__":
    main()
