from flask import Flask, send_from_directory, jsonify, request
import yfinance as yf
import requests
import json, os, datetime

app = Flask(__name__, static_folder='static', static_url_path='')

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/quote')
def quote():
    ticker = request.args.get('ticker', '')
    if not ticker:
        return jsonify({'error': 'no ticker'})
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period='2d')
        price = info.get('currentPrice') or info.get('regularMarketPrice') or (hist['Close'].iloc[-1] if len(hist) > 0 else 0)
        prev = info.get('previousClose') or (hist['Close'].iloc[-2] if len(hist) > 1 else price)
        chg = ((price - prev) / prev) * 100 if prev and prev > 0 else 0
        return jsonify({'symbol': ticker, 'price': round(float(price), 2), 'chg': round(float(chg), 2)})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/watchlist')
def watchlist():
    tickers = request.args.get('tickers', '')
    if not tickers:
        return jsonify([])
    
    ticker_list = [t.strip() for t in tickers.split(',') if t.strip()]
    results = []
    
    for t in ticker_list:
        try:
            tk = yf.Ticker(t)
            info = tk.info or {}
            hist = tk.history(period='3mo')
            
            price = info.get('currentPrice') or info.get('regularMarketPrice') or (hist['Close'].iloc[-1] if len(hist) > 0 else 0)
            prev = info.get('previousClose') or (hist['Close'].iloc[-2] if len(hist) > 1 else price)
            chg = ((price - prev) / prev) * 100 if prev and prev > 0 else 0
            
            # RSI (14)
            rsi = None
            if len(hist) >= 15:
                delta = hist['Close'].diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs.iloc[-1])) if loss.iloc[-1] != 0 else 100
            
            # MA trend (8/13)
            ma_trend = None
            if len(hist) >= 13:
                ma8 = hist['Close'].rolling(8).mean().iloc[-1]
                ma13 = hist['Close'].rolling(13).mean().iloc[-1]
                ma8_prev = hist['Close'].rolling(8).mean().iloc[-2] if len(hist) >= 14 else None
                if ma8_prev and ma8_prev <= ma13 and ma8 > ma13:
                    ma_trend = 'bull'
                elif ma8_prev and ma8_prev >= ma13 and ma8 < ma13:
                    ma_trend = 'bear'
                elif ma8 > ma13:
                    ma_trend = 'bull'
                else:
                    ma_trend = 'bear'
            
            # Signal
            signal = 'NEUTRAL'
            if rsi and rsi < 35 and ma_trend == 'bull':
                signal = 'BUY'
            elif rsi and rsi > 65 and ma_trend == 'bear':
                signal = 'SELL'
            elif ma_trend == 'bull' and rsi and rsi < 55:
                signal = 'BUY'
            elif ma_trend == 'bear' and rsi and rsi > 45:
                signal = 'SELL'
            
            volume = info.get('volume') or (hist['Volume'].iloc[-1] if len(hist) > 0 else 0)
            
            results.append({
                'symbol': t,
                'price': round(float(price), 2),
                'chg': round(float(chg), 2),
                'rsi': round(float(rsi), 1) if rsi else None,
                'ma_trend': ma_trend,
                'volume': int(volume) if volume else 0,
                'signal': signal
            })
        except Exception:
            results.append({'symbol': t, 'price': 0, 'chg': 0, 'rsi': None, 'ma_trend': None, 'volume': 0, 'signal': 'ERROR'})
    
    return jsonify(results)

@app.route('/api/watchlist/save')
def watchlist_save():
    tickers = request.args.get('tickers', '')
    if tickers:
        with open(os.path.join(DATA_DIR, 'watchlist.json'), 'w') as f:
            json.dump(tickers.split(','), f)
    return jsonify({'ok': True})

@app.route('/api/history')
def history():
    ticker = request.args.get('ticker', '')
    period = request.args.get('period', '1mo')
    if not ticker:
        return jsonify([])
    
    period_map = {'1w': '5d', '1mo': '1mo', '3mo': '3mo', '6mo': '6mo', '1y': '1y', '5y': '5y'}
    p = period_map.get(period, '1mo')
    
    # For crypto, adjust symbol
    yf_ticker = ticker
    if ticker == 'BTC-USD' or ticker == 'ETH-USD':
        pass  # Keep as is
    
    try:
        t = yf.Ticker(yf_ticker)
        hist = t.history(period=p)
        result = []
        for idx, row in hist.iterrows():
            result.append({
                'time': idx.strftime('%Y-%m-%d'),
                'open': round(float(row['Open']), 2),
                'high': round(float(row['High']), 2),
                'low': round(float(row['Low']), 2),
                'close': round(float(row['Close']), 2),
                'volume': int(row['Volume'])
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/fundamentals')
def fundamentals():
    ticker = request.args.get('ticker', '')
    if not ticker:
        return jsonify({})
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        return jsonify({
            'pe': info.get('trailingPE') or info.get('forwardPE'),
            'eps': info.get('trailingEps') or info.get('epsTrailingTwelveMonths'),
            'mktCap': info.get('marketCap'),
            'revenue': info.get('totalRevenue') or info.get('revenue'),
            'divYield': info.get('dividendYield'),
            'sector': info.get('sector'),
            'industry': info.get('industry'),
            'name': info.get('shortName') or info.get('longName')
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/news')
def news():
    ticker = request.args.get('ticker', '')
    if not ticker:
        return jsonify([])
    try:
        t = yf.Ticker(ticker)
        news_raw = t.news
        result = []
        for n in (news_raw or []):
            result.append({
                'headline': n.get('title', ''),
                'url': n.get('link', ''),
                'source': n.get('publisher', ''),
                'time': n.get('providerPublishTime', '')
            })
        return jsonify(result)
    except:
        return jsonify([])

@app.route('/api/macro')
def macro():
    result = {}
    try:
        # DXY
        dxy = yf.Ticker('DX-Y.NYB')
        dxy_hist = dxy.history(period='1d')
        if len(dxy_hist) > 0:
            result['dxy'] = round(float(dxy_hist['Close'].iloc[-1]), 2)
    except:
        pass
    try:
        tnx = yf.Ticker('^TNX')
        tnx_hist = tnx.history(period='1d')
        if len(tnx_hist) > 0:
            result['tnx'] = round(float(tnx_hist['Close'].iloc[-1]), 2)
    except:
        pass
    try:
        vix = yf.Ticker('^VIX')
        vix_hist = vix.history(period='1d')
        if len(vix_hist) > 0:
            result['vix'] = round(float(vix_hist['Close'].iloc[-1]), 2)
    except:
        pass
    try:
        wti = yf.Ticker('CL=F')
        wti_hist = wti.history(period='1d')
        if len(wti_hist) > 0:
            result['wti'] = round(float(wti_hist['Close'].iloc[-1]), 2)
    except:
        pass
    try:
        gold = yf.Ticker('GC=F')
        gold_hist = gold.history(period='1d')
        if len(gold_hist) > 0:
            result['gold'] = round(float(gold_hist['Close'].iloc[-1]), 2)
    except:
        pass
    try:
        us2y = yf.Ticker('^2YY')
        us2y_hist = us2y.history(period='1d')
        if len(us2y_hist) > 0:
            result['us2y'] = round(float(us2y_hist['Close'].iloc[-1]), 2)
    except:
        pass
    return jsonify(result)

@app.route('/api/search')
def search():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    try:
        # Use yfinance search
        results = yf.Search(q)
        tickers = []
        if hasattr(results, 'quotes'):
            for q in results.quotes[:8]:
                tickers.append({'symbol': q.get('symbol', ''), 'name': q.get('shortname', q.get('longname', '')), 'type': q.get('quoteType', '')})
        return jsonify(tickers)
    except:
        return jsonify([])

if __name__ == '__main__':
    # Ensure data dir exists
    os.makedirs(DATA_DIR, exist_ok=True)
    watchlist_file = os.path.join(DATA_DIR, 'watchlist.json')
    if not os.path.exists(watchlist_file):
        with open(watchlist_file, 'w') as f:
            json.dump(['NVDA', 'AAPL', 'VRT', 'CEG', 'MRVL', 'TSM', 'AMZN', 'META', 'GOOGL'], f)
    print("🚀 Stock Dashboard running at http://0.0.0.0:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)
