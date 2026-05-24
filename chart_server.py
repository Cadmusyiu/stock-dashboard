#!/usr/bin/env python3
"""
Chart Data Proxy — serves OHLC data for Stock Dashboard
Listens on localhost:8765, fetches from yfinance
Usage: python3 chart_server.py
"""
import json, sys, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import yfinance as yf

PORT = 8765

class ChartHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # CORS headers
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Cache-Control', 'max-age=300')
        self.end_headers()

        if path == '/chart':
            ticker = params.get('ticker', [None])[0]
            period = params.get('period', ['6mo'])[0]
            interval = params.get('interval', ['1d'])[0]

            if not ticker:
                self.wfile.write(json.dumps({'error': 'missing ticker'}).encode())
                return

            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period=period, interval=interval)
                if hist.empty:
                    self.wfile.write(json.dumps({'error': 'no data', 'ticker': ticker}).encode())
                    return

                candles = []
                for idx, row in hist.iterrows():
                    # lightweight-charts: daily → 'YYYY-MM-DD', intraday → unix seconds
                    if interval in ('1d', '1wk', '1mo'):
                        t = idx.strftime('%Y-%m-%d')
                    else:
                        t = int(idx.timestamp())
                    candles.append({
                        'time': t,
                        'open': round(float(row['Open']), 2),
                        'high': round(float(row['High']), 2),
                        'low': round(float(row['Low']), 2),
                        'close': round(float(row['Close']), 2),
                        'volume': int(row['Volume'])
                    })

                self.wfile.write(json.dumps({'ticker': ticker, 'candles': candles}).encode())
            except Exception as e:
                self.wfile.write(json.dumps({'error': str(e), 'ticker': ticker}).encode())
        elif path == '/health':
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        else:
            self.wfile.write(json.dumps({'error': 'unknown endpoint'}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()

    def log_message(self, format, *args):
        pass  # silent

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), ChartHandler)
    print(f'📈 Chart server running on http://127.0.0.1:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down...')
        server.shutdown()
