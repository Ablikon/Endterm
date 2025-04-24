import time
import json
import threading
import random
import sys
from typing import Dict, List, Any, Optional, Tuple
import dash
import dash_bootstrap_components as dbc
from dash import dcc, html, Input, Output, State, callback
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np
import flask
import requests
from adaptive_shield import AdaptiveShield, RateLimitStrategy

shield = AdaptiveShield(
    default_limit=100,
    default_window=60,
    default_strategy=RateLimitStrategy.TOKEN_BUCKET,
    monitor_interval=5,
    auto_adapt=True
)

shield.set_route_limit("/api/public", 200, 60, RateLimitStrategy.SLIDING_WINDOW)
shield.set_route_limit("/api/users", 50, 60, RateLimitStrategy.LEAKY_BUCKET)
shield.set_route_limit("/api/admin", 20, 60, RateLimitStrategy.ADAPTIVE_WINDOW)

server = flask.Flask(__name__)

history = {
    "timestamps": [],
    "requests": [],
    "allowed": [],
    "rejected": [],
    "routes": {
        "/api/public": {"requests": [], "allowed": [], "rejected": []},
        "/api/users": {"requests": [], "allowed": [], "rejected": []},
        "/api/admin": {"requests": [], "allowed": [], "rejected": []}
    },
    "clients": {}
}

active_simulations = {}

@server.route('/api/public')
def public_endpoint():
    client_id = request.headers.get('X-Client-ID', 'dashboard')
    allowed = shield.check_request(client_id, '/api/public')
    
    if not allowed:
        return "Rate limit exceeded", 429
    
    return {
        "status": "success",
        "message": "Public API access",
        "timestamp": time.time()
    }

@server.route('/api/users')
def users_endpoint():
    client_id = request.headers.get('X-Client-ID', 'dashboard')
    allowed = shield.check_request(client_id, '/api/users')
    
    if not allowed:
        return "Rate limit exceeded", 429
    
    return {
        "status": "success",
        "message": "Users API access",
        "timestamp": time.time()
    }

@server.route('/api/admin')
def admin_endpoint():
    client_id = request.headers.get('X-Client-ID', 'dashboard')
    allowed = shield.check_request(client_id, '/api/admin')
    
    if not allowed:
        return "Rate limit exceeded", 429
    
    return {
        "status": "success",
        "message": "Admin API access",
        "timestamp": time.time()
    }

def update_history():
    current_time = time.time()
    
    stats = shield.get_global_stats()
    
    history["timestamps"].append(current_time)
    history["requests"].append(stats.get("total_requests", 0))
    history["allowed"].append(stats.get("allowed_requests", 0))
    history["rejected"].append(stats.get("rejected_requests", 0))
    
    for route in history["routes"]:
        route_stats = shield.get_route_stats(route)
        history["routes"][route]["requests"].append(route_stats.get("total_requests", 0))
        history["routes"][route]["allowed"].append(route_stats.get("allowed_requests", 0))
        history["routes"][route]["rejected"].append(route_stats.get("rejected_requests", 0))
    
    for client_id in list(active_simulations.keys()):
        if client_id not in history["clients"]:
            history["clients"][client_id] = {
                "requests": [],
                "allowed": [],
                "rejected": []
            }
        
        client_stats = shield.get_client_stats(client_id)
        history["clients"][client_id]["requests"].append(client_stats.get("total_requests", 0))
        history["clients"][client_id]["allowed"].append(client_stats.get("allowed_requests", 0))
        history["clients"][client_id]["rejected"].append(client_stats.get("rejected_requests", 0))
    
    while len(history["timestamps"]) > 100:
        history["timestamps"].pop(0)
        history["requests"].pop(0)
        history["allowed"].pop(0)
        history["rejected"].pop(0)
        
        for route in history["routes"]:
            history["routes"][route]["requests"].pop(0)
            history["routes"][route]["allowed"].pop(0)
            history["routes"][route]["rejected"].pop(0)
        
        for client_id in history["clients"]:
            if history["clients"][client_id]["requests"]:
                history["clients"][client_id]["requests"].pop(0)
                history["clients"][client_id]["allowed"].pop(0)
                history["clients"][client_id]["rejected"].pop(0)

app = dash.Dash(
    __name__,
    server=server,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    url_base_pathname='/',
    meta_tags=[{'name': 'viewport', 'content': 'width=device-width, initial-scale=1'}]
)
app.title = "AdaptiveShield Monitoring Dashboard"

navbar = dbc.NavbarSimple(
    children=[
        dbc.NavItem(dbc.NavLink("GitHub", href="https://github.com/yourusername/adaptive-shield")),
        dbc.NavItem(dbc.NavLink("Documentation", href="#")),
    ],
    brand="AdaptiveShield Monitoring Dashboard",
    brand_href="#",
    color="primary",
    dark=True,
)

tab_style = {
    'borderBottom': '1px solid #d6d6d6',
    'padding': '6px',
    'fontWeight': 'bold'
}

tab_selected_style = {
    'borderTop': '1px solid #d6d6d6',
    'borderBottom': '1px solid #d6d6d6',
    'backgroundColor': '#119DFF',
    'color': 'white',
    'padding': '6px'
}

app.layout = html.Div([
    navbar,
    dcc.Interval(
        id='interval-component',
        interval=1*1000,
        n_intervals=0
    ),
    dbc.Container([
        dbc.Row([
            dbc.Col([
                html.H3("Global Metrics", className="mt-4"),
                html.Div(id="global-metrics"),
                html.H4("Traffic Overview", className="mt-4"),
                dcc.Graph(id="requests-graph"),
                html.Div([
                    dcc.Tabs(id="tabs", value='routes', children=[
                        dcc.Tab(label='Routes', value='routes', style=tab_style, selected_style=tab_selected_style),
                        dcc.Tab(label='Clients', value='clients', style=tab_style, selected_style=tab_selected_style),
                        dcc.Tab(label='Configuration', value='config', style=tab_style, selected_style=tab_selected_style),
                        dcc.Tab(label='Traffic Simulator', value='simulator', style=tab_style, selected_style=tab_selected_style),
                    ]),
                    html.Div(id='tabs-content')
                ]),
            ])
        ])
    ])
])

@app.callback(
    Output("global-metrics", "children"),
    Input("interval-component", "n_intervals")
)
def update_global_metrics(n):
    update_history()
    
    stats = shield.get_global_stats()
    
    total_requests = stats.get("total_requests", 0)
    allowed_requests = stats.get("allowed_requests", 0)
    rejected_requests = stats.get("rejected_requests", 0)
    
    rejection_rate = 0
    if total_requests > 0:
        rejection_rate = rejected_requests / total_requests
    
    card_content = [
        dbc.CardHeader("Rate Limiting Overview"),
        dbc.CardBody(
            [
                dbc.Row([
                    dbc.Col([
                        html.H5("Total Requests", className="card-title"),
                        html.P(f"{total_requests}", className="card-text")
                    ], width=3),
                    dbc.Col([
                        html.H5("Allowed", className="card-title"),
                        html.P(f"{allowed_requests}", className="card-text")
                    ], width=3),
                    dbc.Col([
                        html.H5("Rejected", className="card-title"),
                        html.P(f"{rejected_requests}", className="card-text")
                    ], width=3),
                    dbc.Col([
                        html.H5("Rejection Rate", className="card-title"),
                        html.P(f"{rejection_rate:.2%}", className="card-text", 
                              style={"color": "red" if rejection_rate > 0.1 else "green"})
                    ], width=3),
                ]),
            ]
        ),
    ]
    
    return dbc.Card(card_content, className="mb-3")

@app.callback(
    Output("requests-graph", "figure"),
    Input("interval-component", "n_intervals")
)
def update_graph(n):
    df = pd.DataFrame({
        "Time": history["timestamps"],
        "Total": history["requests"],
        "Allowed": history["allowed"],
        "Rejected": history["rejected"]
    })
    
    if len(df) > 1:
        df["Requests/s"] = [0] + [(df["Total"][i] - df["Total"][i-1]) / 
                                 (df["Time"][i] - df["Time"][i-1])
                                 if df["Time"][i] > df["Time"][i-1] else 0 
                                 for i in range(1, len(df))]
    else:
        df["Requests/s"] = [0] * len(df)
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=df["Time"],
        y=df["Requests/s"],
        mode='lines',
        name='Requests/s',
        line=dict(color='blue')
    ))
    
    fig.add_trace(go.Scatter(
        x=df["Time"],
        y=[(r / t if t > 0 else 0) * 100 for r, t in zip(df["Rejected"], df["Total"])],
        mode='lines',
        name='Rejection %',
        line=dict(color='red'),
        yaxis='y2'
    ))
    
    fig.update_layout(
        title='Traffic Rate and Rejection Percentage',
        xaxis_title='Time',
        yaxis_title='Requests per Second',
        yaxis2=dict(
            title='Rejection %',
            overlaying='y',
            side='right',
            range=[0, 100]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )
    
    return fig

@app.callback(
    Output('tabs-content', 'children'),
    Input('tabs', 'value')
)
def render_content(tab):
    if tab == 'routes':
        return html.Div([
            html.H4("Route Metrics", className="mt-3"),
            html.P("Select a route to view its metrics:"),
            dcc.Dropdown(
                id='route-dropdown',
                options=[{'label': route, 'value': route} for route in history["routes"].keys()],
                value='/api/public'
            ),
            html.Div(id='route-metrics')
        ])
    elif tab == 'clients':
        clients = list(history["clients"].keys())
        return html.Div([
            html.H4("Client Metrics", className="mt-3"),
            html.P("Select a client to view its metrics:"),
            dcc.Dropdown(
                id='client-dropdown',
                options=[{'label': client, 'value': client} for client in clients],
                value=clients[0] if clients else None
            ),
            html.Div(id='client-metrics')
        ])
    elif tab == 'config':
        return html.Div([
            html.H4("Rate Limiting Configuration", className="mt-3"),
            html.P("Modify rate limiting settings for specific routes:"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Route:"),
                    dcc.Dropdown(
                        id='config-route-dropdown',
                        options=[{'label': route, 'value': route} for route in history["routes"].keys()],
                        value='/api/public'
                    ),
                ], width=12),
            ]),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Request Limit:"),
                    dbc.Input(id='limit-input', type='number', value=200),
                ], width=4),
                dbc.Col([
                    dbc.Label("Window (seconds):"),
                    dbc.Input(id='window-input', type='number', value=60),
                ], width=4),
                dbc.Col([
                    dbc.Label("Strategy:"),
                    dcc.Dropdown(
                        id='strategy-dropdown',
                        options=[
                            {'label': 'Token Bucket', 'value': 'TOKEN_BUCKET'},
                            {'label': 'Sliding Window', 'value': 'SLIDING_WINDOW'},
                            {'label': 'Leaky Bucket', 'value': 'LEAKY_BUCKET'},
                            {'label': 'Adaptive Window', 'value': 'ADAPTIVE_WINDOW'},
                        ],
                        value='TOKEN_BUCKET'
                    ),
                ], width=4),
            ]),
            dbc.Button("Apply Configuration", id='apply-config-button', color="primary", className="mt-3"),
            html.Div(id='config-status', className="mt-2")
        ])
    elif tab == 'simulator':
        return html.Div([
            html.H4("Traffic Simulator", className="mt-3"),
            html.P("Generate test traffic to evaluate rate limiting performance:"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Client ID:"),
                    dbc.Input(id='client-id-input', type='text', value=f'test_client_{random.randint(1000, 9999)}'),
                ], width=12),
            ]),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Target Route:"),
                    dcc.Dropdown(
                        id='target-route-dropdown',
                        options=[{'label': route, 'value': route} for route in history["routes"].keys()],
                        value='/api/public'
                    ),
                ], width=4),
                dbc.Col([
                    dbc.Label("Requests Per Second:"),
                    dbc.Input(id='rps-input', type='number', value=5),
                ], width=4),
                dbc.Col([
                    dbc.Label("Duration (seconds):"),
                    dbc.Input(id='duration-input', type='number', value=30),
                ], width=4),
            ]),
            dbc.Button("Start Simulation", id='start-sim-button', color="success", className="mt-3"),
            html.Div(id='sim-status', className="mt-2"),
            html.Div(id='active-simulations')
        ])

@app.callback(
    Output("route-metrics", "children"),
    [Input("interval-component", "n_intervals"),
     Input("route-dropdown", "value")]
)
def update_route_metrics(n, route):
    if not route:
        return html.Div("No route selected")
    
    route_stats = shield.get_route_stats(route)
    
    total_requests = route_stats.get("total_requests", 0)
    allowed_requests = route_stats.get("allowed_requests", 0)
    rejected_requests = route_stats.get("rejected_requests", 0)
    
    rejection_rate = 0
    if total_requests > 0:
        rejection_rate = rejected_requests / total_requests
    
    config = route_stats.get("config", {})
    limit = config.get("limit", shield.default_limit)
    window = config.get("window", shield.default_window)
    strategy = config.get("strategy", shield.default_strategy.name)
    
    df = pd.DataFrame({
        "Time": history["timestamps"][-30:],
        "Requests": history["routes"][route]["requests"][-30:],
        "Allowed": history["routes"][route]["allowed"][-30:],
        "Rejected": history["routes"][route]["rejected"][-30:]
    })
    
    if len(df) > 1:
        df["Requests/s"] = [0] + [(df["Requests"][i] - df["Requests"][i-1]) / 
                                 (df["Time"][i] - df["Time"][i-1])
                                 if df["Time"][i] > df["Time"][i-1] else 0 
                                 for i in range(1, len(df))]
    else:
        df["Requests/s"] = [0] * len(df)
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=df["Time"],
        y=df["Requests/s"],
        mode='lines',
        name='Requests/s',
        line=dict(color='blue')
    ))
    
    fig.add_trace(go.Bar(
        x=df["Time"],
        y=[(r / t if t > 0 else 0) * 100 for r, t in zip(df["Rejected"], df["Requests"])],
        name='Rejection %',
        marker_color='red',
        yaxis='y2'
    ))
    
    fig.update_layout(
        title=f'Traffic for {route}',
        xaxis_title='Time',
        yaxis_title='Requests per Second',
        yaxis2=dict(
            title='Rejection %',
            overlaying='y',
            side='right',
            range=[0, 100]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        height=300
    )
    
    card_content = [
        dbc.CardHeader(f"Route: {route}"),
        dbc.CardBody(
            [
                dbc.Row([
                    dbc.Col([
                        html.H5("Configuration", className="card-title"),
                        html.P(f"Limit: {limit} requests / {window}s", className="card-text"),
                        html.P(f"Strategy: {strategy}", className="card-text"),
                    ], width=4),
                    dbc.Col([
                        html.H5("Request Statistics", className="card-title"),
                        html.P(f"Total: {total_requests}", className="card-text"),
                        html.P(f"Allowed: {allowed_requests}", className="card-text"),
                        html.P(f"Rejected: {rejected_requests} ({rejection_rate:.2%})", 
                              className="card-text", 
                              style={"color": "red" if rejection_rate > 0.1 else "green"}),
                    ], width=8),
                ]),
                dcc.Graph(figure=fig)
            ]
        ),
    ]
    
    return dbc.Card(card_content, className="mb-3")

@app.callback(
    Output("config-status", "children"),
    [Input("apply-config-button", "n_clicks")],
    [State("config-route-dropdown", "value"),
     State("limit-input", "value"),
     State("window-input", "value"),
     State("strategy-dropdown", "value")]
)
def apply_configuration(n_clicks, route, limit, window, strategy):
    if not n_clicks:
        return ""
    
    if not route or not limit or not window or not strategy:
        return html.Div("Please fill all configuration fields", style={"color": "red"})
    
    try:
        shield.set_route_limit(
            route, 
            limit, 
            window, 
            getattr(RateLimitStrategy, strategy)
        )
        
        return html.Div(
            f"Configuration applied: {route} - {limit} requests / {window}s using {strategy}",
            style={"color": "green"}
        )
    except Exception as e:
        return html.Div(f"Error: {str(e)}", style={"color": "red"})

def simulate_traffic(client_id, route, rps, duration):
    active_simulations[client_id] = {
        "route": route,
        "rps": rps,
        "start_time": time.time(),
        "end_time": time.time() + duration,
        "requests": 0,
        "allowed": 0,
        "rejected": 0
    }
    
    delay = 1.0 / rps
    end_time = time.time() + duration
    
    while time.time() < end_time and client_id in active_simulations:
        start_request = time.time()
        
        try:
            allowed = shield.check_request(client_id, route)
            
            active_simulations[client_id]["requests"] += 1
            if allowed:
                active_simulations[client_id]["allowed"] += 1
            else:
                active_simulations[client_id]["rejected"] += 1
        
        except Exception as e:
            print(f"Error in simulation: {e}")
        
        request_time = time.time() - start_request
        sleep_time = max(0, delay - request_time)
        
        if sleep_time > 0:
            time.sleep(sleep_time)
    
    if client_id in active_simulations:
        active_simulations[client_id]["end_time"] = time.time()

@app.callback(
    Output("sim-status", "children"),
    [Input("start-sim-button", "n_clicks")],
    [State("client-id-input", "value"),
     State("target-route-dropdown", "value"),
     State("rps-input", "value"),
     State("duration-input", "value")]
)
def start_simulation(n_clicks, client_id, route, rps, duration):
    if not n_clicks:
        return ""
    
    if not client_id or not route or not rps or not duration:
        return html.Div("Please fill all simulation fields", style={"color": "red"})
    
    if client_id in active_simulations:
        return html.Div(f"A simulation is already running for client {client_id}", style={"color": "orange"})
    
    try:
        threading.Thread(
            target=simulate_traffic,
            args=(client_id, route, rps, duration),
            daemon=True
        ).start()
        
        return html.Div(
            f"Simulation started: {client_id} - {rps} req/s to {route} for {duration}s",
            style={"color": "green"}
        )
    except Exception as e:
        return html.Div(f"Error starting simulation: {str(e)}", style={"color": "red"})

@app.callback(
    Output("active-simulations", "children"),
    Input("interval-component", "n_intervals")
)
def update_active_simulations(n):
    current_time = time.time()
    
    to_remove = []
    for client_id, sim in active_simulations.items():
        if current_time > sim["end_time"]:
            to_remove.append(client_id)
    
    for client_id in to_remove:
        del active_simulations[client_id]
    
    if not active_simulations:
        return html.Div("No active simulations")
    
    rows = []
    for client_id, sim in active_simulations.items():
        progress = min(100, ((current_time - sim["start_time"]) / 
                          (sim["end_time"] - sim["start_time"])) * 100)
        
        rejection_rate = 0
        if sim["requests"] > 0:
            rejection_rate = sim["rejected"] / sim["requests"]
        
        rows.append(
            html.Tr([
                html.Td(client_id),
                html.Td(sim["route"]),
                html.Td(f"{sim['rps']} req/s"),
                html.Td(f"{sim['requests']}"),
                html.Td(f"{sim['allowed']}"),
                html.Td(f"{sim['rejected']}"),
                html.Td(f"{rejection_rate:.2%}"),
                html.Td([
                    dbc.Progress(value=progress, style={"height": "15px"})
                ])
            ])
        )
    
    table = dbc.Table(
        [
            html.Thead(
                html.Tr([
                    html.Th("Client ID"),
                    html.Th("Route"),
                    html.Th("Rate"),
                    html.Th("Requests"),
                    html.Th("Allowed"),
                    html.Th("Rejected"),
                    html.Th("Rejection Rate"),
                    html.Th("Progress"),
                ])
            ),
            html.Tbody(rows)
        ],
        bordered=True,
        hover=True,
        responsive=True,
        striped=True,
    )
    
    return html.Div([
        html.H5("Active Simulations", className="mt-3"),
        table
    ])

@app.callback(
    Output("client-metrics", "children"),
    [Input("interval-component", "n_intervals"),
     Input("client-dropdown", "value")]
)
def update_client_metrics(n, client_id):
    if not client_id or client_id not in history["clients"]:
        return html.Div("No client selected or no client data available")
    
    client_stats = shield.get_client_stats(client_id)
    
    total_requests = client_stats.get("total_requests", 0)
    allowed_requests = client_stats.get("allowed_requests", 0)
    rejected_requests = client_stats.get("rejected_requests", 0)
    
    rejection_rate = 0
    if total_requests > 0:
        rejection_rate = rejected_requests / total_requests
    
    df = pd.DataFrame({
        "Time": history["timestamps"][-30:],
        "Requests": history["clients"][client_id]["requests"][-30:],
        "Allowed": history["clients"][client_id]["allowed"][-30:],
        "Rejected": history["clients"][client_id]["rejected"][-30:]
    })
    
    if len(df) > 1:
        df["Requests/s"] = [0] + [(df["Requests"][i] - df["Requests"][i-1]) / 
                                 (df["Time"][i] - df["Time"][i-1])
                                 if df["Time"][i] > df["Time"][i-1] else 0 
                                 for i in range(1, len(df))]
    else:
        df["Requests/s"] = [0] * len(df)
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=df["Time"],
        y=df["Requests/s"],
        mode='lines',
        name='Requests/s',
        line=dict(color='blue')
    ))
    
    fig.add_trace(go.Bar(
        x=df["Time"],
        y=[(r / t if t > 0 else 0) * 100 for r, t in zip(df["Rejected"], df["Requests"])],
        name='Rejection %',
        marker_color='red',
        yaxis='y2'
    ))
    
    fig.update_layout(
        title=f'Traffic for Client: {client_id}',
        xaxis_title='Time',
        yaxis_title='Requests per Second',
        yaxis2=dict(
            title='Rejection %',
            overlaying='y',
            side='right',
            range=[0, 100]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        height=300
    )
    
    card_content = [
        dbc.CardHeader(f"Client: {client_id}"),
        dbc.CardBody(
            [
                dbc.Row([
                    dbc.Col([
                        html.H5("Request Statistics", className="card-title"),
                        html.P(f"Total: {total_requests}", className="card-text"),
                        html.P(f"Allowed: {allowed_requests}", className="card-text"),
                        html.P(f"Rejected: {rejected_requests} ({rejection_rate:.2%})", 
                              className="card-text", 
                              style={"color": "red" if rejection_rate > 0.1 else "green"}),
                    ], width=12),
                ]),
                dcc.Graph(figure=fig)
            ]
        ),
    ]
    
    return dbc.Card(card_content, className="mb-3")

if __name__ == '__main__':
    app.run_server(host='0.0.0.0', port=8050, debug=True)