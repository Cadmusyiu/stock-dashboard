#!/usr/bin/env python3
"""
Stock Dashboard — Flask Server
Serves the static frontend + API endpoints.
"""

import os, json, random, math
from datetime import datetime, timezone
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ================================================================
# MACRO (simulated)
# ================================================================
def sim_price(base, vol=0.002):
    chg = (random.random()-0.5)*2*vol*base
    return round(base+chg,2), round(chg/base*100,2)

macro = {
    'btc':{'price':77432.50}, 'eth':{'price':2184.30}, 'spy':{'price':584.17},
    'dxy':{'value':105.38}, 'vix':{'value':14.27}, 'us10y':{'value':4.32}, 'us2y':{'value':3.88},
}

# ================================================================
# PORTFOLIO DATA
# ================================================================
PORTFOLIO = {
    'buffett': {
        'name':'Warren Buffett','aum':'$1.2T',
        'topHoldings':['AAPL','BAC','AXP','KO','OXY'],
        'holdings':[
            {'ticker':'AAPL','price':302.25,'chg':1.10,'rsi':84.8,'signal':'SELL','weight':42.5},
            {'ticker':'BAC','price':46.72,'chg':0.65,'rsi':62.3,'signal':'NEUTRAL','weight':11.8},
            {'ticker':'AXP','price':273.40,'chg':1.42,'rsi':68.5,'signal':'NEUTRAL','weight':8.2},
            {'ticker':'KO','price':70.15,'chg':-0.32,'rsi':38.2,'signal':'BUY','weight':7.6},
            {'ticker':'OXY','price':48.88,'chg':-1.85,'rsi':28.4,'signal':'BUY','weight':5.4},
            {'ticker':'CVX','price':155.30,'chg':-0.58,'rsi':41.0,'signal':'NEUTRAL','weight':4.1},
            {'ticker':'MCO','price':468.20,'chg':2.75,'rsi':55.7,'signal':'NEUTRAL','weight':3.9},
        ],
    },
    'salp': {
        'name':'SALP (Situational Awareness LP)','aum':'$13.68B',
        'topHoldings':['NVDA','VRT','CEG','MRVL','TSM'],
        'holdings':[
            {'ticker':'NVDA','price':223.47,'chg':1.30,'rsi':69.8,'signal':'NEUTRAL','weight':42.5},
            {'ticker':'VRT','price':315.67,'chg':-2.16,'rsi':45.7,'signal':'BUY','weight':10.0},
            {'ticker':'CEG','price':281.26,'chg':7.90,'rsi':35.0,'signal':'NEUTRAL','weight':8.0},
            {'ticker':'MRVL','price':85.50,'chg':0.50,'rsi':50.0,'signal':'NEUTRAL','weight':7.0},
            {'ticker':'TSM','price':195.00,'chg':-1.20,'rsi':45.0,'signal':'BUY','weight':6.5},
            {'ticker':'AMD','price':118.30,'chg':-3.45,'rsi':32.1,'signal':'BUY','weight':5.2},
            {'ticker':'ANET','price':98.75,'chg':4.82,'rsi':72.0,'signal':'SELL','weight':4.8},
            {'ticker':'AVGO','price':182.40,'chg':1.05,'rsi':58.3,'signal':'NEUTRAL','weight':4.2},
        ],
    },
    'trump': {
        'name':'Trump Media & Family Office','aum':'$4.2B',
        'topHoldings':['DJT','BTC','ETH','AAPL','MSFT'],
        'holdings':[
            {'ticker':'DJT','price':32.45,'chg':5.60,'rsi':78.2,'signal':'SELL','weight':35.0},
            {'ticker':'BTC','price':77432.00,'chg':1.24,'rsi':55.0,'signal':'NEUTRAL','weight':20.0},
            {'ticker':'ETH','price':2184.30,'chg':-0.78,'rsi':42.3,'signal':'BUY','weight':12.0},
            {'ticker':'AAPL','price':302.25,'chg':1.10,'rsi':84.8,'signal':'SELL','weight':8.5},
            {'ticker':'MSFT','price':468.80,'chg':0.12,'rsi':61.0,'signal':'NEUTRAL','weight':7.0},
        ],
    },
    'cathie': {
        'name':'Cathie Wood (ARK Invest)','aum':'$14.6B',
        'topHoldings':['TSLA','ROKU','COIN','ZM','RBLX'],
        'holdings':[
            {'ticker':'TSLA','price':278.50,'chg':-2.40,'rsi':40.1,'signal':'BUY','weight':15.2},
            {'ticker':'ROKU','price':72.80,'chg':1.15,'rsi':52.6,'signal':'NEUTRAL','weight':8.7},
            {'ticker':'COIN','price':195.20,'chg':4.30,'rsi':71.4,'signal':'SELL','weight':7.5},
            {'ticker':'ZM','price':62.45,'chg':-0.88,'rsi':44.0,'signal':'BUY','weight':6.8},
            {'ticker':'RBLX','price':45.10,'chg':3.22,'rsi':66.5,'signal':'NEUTRAL','weight':6.2},
            {'ticker':'SQ','price':84.60,'chg':-1.50,'rsi':38.7,'signal':'BUY','weight':5.9},
            {'ticker':'CRSP','price':72.30,'chg':8.75,'rsi':80.2,'signal':'SELL','weight':3.8},
        ],
    },
    'drucken': {
        'name':'Stanley Druckenmiller','aum':'$7.8B',
        'topHoldings':['NVDA','MSFT','AMZN','GOOGL','META'],
        'holdings':[
            {'ticker':'NVDA','price':223.47,'chg':1.30,'rsi':69.8,'signal':'NEUTRAL','weight':22.0},
            {'ticker':'MSFT','price':468.80,'chg':0.12,'rsi':61.0,'signal':'NEUTRAL','weight':15.5},
            {'ticker':'AMZN','price':205.10,'chg':-0.55,'rsi':47.3,'signal':'NEUTRAL','weight':12.0},
            {'ticker':'GOOGL','price':172.30,'chg':1.80,'rsi':64.2,'signal':'BUY','weight':10.5},
            {'ticker':'META','price':565.20,'chg':2.45,'rsi':73.0,'signal':'SELL','weight':9.0},
            {'ticker':'COIN','price':195.20,'chg':4.30,'rsi':71.4,'signal':'SELL','weight':4.0},
        ],
    },
    'burry': {
        'name':'Michael Burry (Scion)','aum':'$1.1B',
        'topHoldings':['GEO','QRTEA','GME','JD','BABA'],
        'holdings':[
            {'ticker':'GEO','price':28.45,'chg':3.30,'rsi':76.5,'signal':'SELL','weight':25.0},
            {'ticker':'QRTEA','price':0.42,'chg':-8.70,'rsi':15.2,'signal':'BUY','weight':18.0},
            {'ticker':'GME','price':24.50,'chg':1.85,'rsi':55.8,'signal':'NEUTRAL','weight':12.0},
            {'ticker':'JD','price':38.60,'chg':-1.20,'rsi':40.3,'signal':'BUY','weight':8.5},
            {'ticker':'BABA','price':112.30,'chg':0.80,'rsi':48.0,'signal':'NEUTRAL','weight':7.5},
        ],
    },
    'ackman': {
        'name':'Bill Ackman (Pershing Square)','aum':'$9.4B',
        'topHoldings':['CMG','GOOGL','SPGI','HLT','PFE'],
        'holdings':[
            {'ticker':'CMG','price':62.50,'chg':1.45,'rsi':59.2,'signal':'NEUTRAL','weight':18.5},
            {'ticker':'GOOGL','price':172.30,'chg':1.80,'rsi':64.2,'signal':'BUY','weight':14.0},
            {'ticker':'SPGI','price':528.40,'chg':-0.30,'rsi':47.8,'signal':'NEUTRAL','weight':12.0},
            {'ticker':'HLT','price':236.10,'chg':0.55,'rsi':52.1,'signal':'NEUTRAL','weight':10.5},
            {'ticker':'PFE','price':26.80,'chg':-2.15,'rsi':28.0,'signal':'BUY','weight':8.2},
            {'ticker':'UBER','price':72.35,'chg':0.85,'rsi':54.3,'signal':'NEUTRAL','weight':6.0},
        ],
    },
    'custom': {
        'name':'Custom Watchlist','aum':'Personal',
        'topHoldings':['BTC','ETH','SOL','NVDA','AAPL'],
        'holdings':[
            {'ticker':'BTC','price':77432.00,'chg':1.24,'rsi':55.0,'signal':'NEUTRAL','weight':30.0},
            {'ticker':'ETH','price':2184.30,'chg':-0.78,'rsi':42.3,'signal':'BUY','weight':20.0},
            {'ticker':'SOL','price':168.45,'chg':3.50,'rsi':67.0,'signal':'NEUTRAL','weight':15.0},
            {'ticker':'NVDA','price':223.47,'chg':1.30,'rsi':69.8,'signal':'NEUTRAL','weight':10.0},
            {'ticker':'AAPL','price':302.25,'chg':1.10,'rsi':84.8,'signal':'SELL','weight':8.0},
            {'ticker':'TSLA','price':278.50,'chg':-2.40,'rsi':40.1,'signal':'BUY','weight':7.0},
        ],
    },
}

# ================================================================
# ROUTES
# ================================================================
@app.route('/')
def index():
    return send_from_directory('.','index.html')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static',path)

@app.route('/style.css')
def serve_css():
    return send_from_directory('static','style.css')

@app.route('/api/macro')
def api_macro():
    btc = sim_price(macro['btc']['price'],0.003)
    eth = sim_price(macro['eth']['price'],0.004)
    spy = sim_price(macro['spy']['price'],0.002)
    return jsonify({
        'btc':{'price':btc[0],'chg':btc[1]},
        'eth':{'price':eth[0],'chg':eth[1]},
        'spy':{'price':spy[0],'chg':spy[1]},
        'timestamp':datetime.now(timezone.utc).isoformat(),
    })

@app.route('/api/portfolio/<mgr>')
def api_portfolio(mgr):
    d = PORTFOLIO.get(mgr)
    return jsonify(d) if d else (jsonify({'error':'not found'}),404)

@app.route('/api/managers')
def api_managers():
    return jsonify([{'id':k,'name':v['name']} for k,v in PORTFOLIO.items()])

@app.route('/api/chart/<ticker>')
def api_chart(ticker):
    days = request.args.get('days',30,type=int)
    base = request.args.get('base',100.0,type=float)
    for v in PORTFOLIO.values():
        for h in v['holdings']:
            if h['ticker']==ticker: base=h['price']; break
    data=[]; p=base; now=datetime.now(timezone.utc)
    for i in range(days,-1,-1):
        ts=int(now.timestamp())-i*86400
        p+=p*0.025*(random.random()-0.5)*2
        p=max(p,base*0.5)
        if p>base*2: p=base*2-abs(p-base)
        data.append({'time':ts,'value':round(p,2)})
    return jsonify({'ticker':ticker,'data':data})

# ================================================================
# MAIN
# ================================================================
if __name__=='__main__':
    port=int(os.environ.get('PORT',5001))
    print(f'🚀 Stock Dashboard @ http://localhost:{port}')
    app.run(host='0.0.0.0',port=port,debug=True)
