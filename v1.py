import sys
import time
import pyupbit
from openai import OpenAI
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLineEdit, QLabel
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QPixmap
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from mplfinance.original_flavor import candlestick_ohlc
import pandas as pd
from io import BytesIO

class BalanceThread(QThread):
    update_balance = pyqtSignal(dict)

    def __init__(self, upbit):
        super().__init__()
        self.upbit = upbit
        self.is_running = True

    def run(self):
        while self.is_running:
            try:
                krw_balance = self.upbit.get_balance("KRW")
                btc_balance = self.upbit.get_balance("KRW-BTC")
                btc_price = pyupbit.get_current_price("KRW-BTC")
                balance_dict = {
                    "krw": f"{krw_balance:,.0f} KRW",
                    "btc": f"{btc_balance:.8f} BTC",
                    "price": f"{btc_price:,.0f} KRW"
                }
                self.update_balance.emit(balance_dict)
                time.sleep(10)
            except Exception as e:
                self.update_balance.emit({"error": f"잔액 조회 오류: {str(e)}"})
                time.sleep(10)

    def stop(self):
        self.is_running = False

class TradingThread(QThread):
    update_signal = pyqtSignal(str)
    update_chart = pyqtSignal(object)

    def __init__(self, upbit, openai_client):
        super().__init__()
        self.upbit = upbit
        self.openai_client = openai_client
        self.is_running = True

    def run(self):
        while self.is_running:
            self.ai_trading()
            time.sleep(600)  # 10분 대기

    def stop(self):
        self.is_running = False

    def ai_trading(self):
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute1", count=60)
        self.update_chart.emit(df)

        response = self.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": """You are an expert in Bitcoin trading and technical analysis. Analyze the provided 1-minute interval Bitcoin price chart data for the last hour and make a decision to buy, sell, or hold Bitcoin. Consider the following factors:

1. Short-term trend analysis: Identify the current trend (bullish, bearish, or sideways).
2. Support and resistance levels: Identify key price levels in the short term.
3. Moving averages: Analyze very short-term moving averages and their crossovers.
4. Volume analysis: Evaluate the relationship between price movements and trading volume.
5. Momentum indicators: Use relevant indicators such as RSI or MACD for short-term momentum.
6. Volatility: Consider short-term price volatility and any sudden price movements.

Provide your decision and a detailed explanation of your analysis. Your response should be in this format:
Decision: [buy/sell/hold]
Reason: [Detailed explanation of your analysis and reasoning]"""
                },
                {
                    "role": "user",
                    "content": df.to_json()
                }
            ]
        )
        result = response.choices[0].message.content

        # Parse the result
        decision = ""
        reason = ""
        for line in result.split('\n'):
            if line.startswith("Decision:"):
                decision = line.split(":")[1].strip().lower()
            elif line.startswith("Reason:"):
                reason = line.split(":", 1)[1].strip()

        self.update_signal.emit(f"AI Decision: {decision.upper()}\nReason: {reason}")

        if decision == "buy":
            my_krw = self.upbit.get_balance("KRW")
            if my_krw * 0.9995 > 5000:
                order = self.upbit.buy_market_order("KRW-BTC", my_krw * 0.9995)
                self.update_signal.emit(f"Buy Order Executed: {order}")
            else:
                self.update_signal.emit("Buy Order Failed: Insufficient KRW")
        elif decision == "sell":
            my_btc = self.upbit.get_balance("KRW-BTC")
            current_price = pyupbit.get_orderbook(ticker="KRW-BTC")['orderbook_units'][0]["ask_price"]
            if my_btc * current_price > 5000:
                order = self.upbit.sell_market_order("KRW-BTC", my_btc)
                self.update_signal.emit(f"Sell Order Executed: {order}")
            else:
                self.update_signal.emit("Sell Order Failed: Insufficient BTC")
        else:
            self.update_signal.emit("Hold Position")

class TradingApp(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.chart_timer = QTimer(self)
        self.chart_timer.timeout.connect(self.update_chart_timer)
        self.chart_timer.start(60000)  # 60000 ms = 1분

    def initUI(self):
        self.setWindowTitle('Bitcoin_trading by GMR')
        self.setGeometry(300, 300, 800, 600)

        layout = QVBoxLayout()

        self.upbit_access_input = QLineEdit(self)
        self.upbit_secret_input = QLineEdit(self)
        self.openai_api_input = QLineEdit(self)
        self.upbit_secret_input.setEchoMode(QLineEdit.Password)
        self.openai_api_input.setEchoMode(QLineEdit.Password)

        layout.addWidget(QLabel('Upbit Access Key:'))
        layout.addWidget(self.upbit_access_input)
        layout.addWidget(QLabel('Upbit Secret Key:'))
        layout.addWidget(self.upbit_secret_input)
        layout.addWidget(QLabel('OpenAI API Key:'))
        layout.addWidget(self.openai_api_input)

        button_layout = QHBoxLayout()
        self.start_button = QPushButton('Start Trading', self)
        self.start_button.clicked.connect(self.start_trading)
        button_layout.addWidget(self.start_button)

        self.stop_button = QPushButton('Stop Trading', self)
        self.stop_button.clicked.connect(self.stop_trading)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)

        layout.addLayout(button_layout)

        self.krw_label = QLabel('KRW Balance: Not available', self)
        self.btc_label = QLabel('BTC Balance: Not available', self)
        self.price_label = QLabel('BTC Price: Not available', self)
        layout.addWidget(self.krw_label)
        layout.addWidget(self.btc_label)
        layout.addWidget(self.price_label)

        self.chart_label = QLabel(self)
        layout.addWidget(self.chart_label)

        self.log_text = QTextEdit(self)
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        self.setLayout(layout)

    def start_trading(self):
        upbit_access = self.upbit_access_input.text()
        upbit_secret = self.upbit_secret_input.text()
        openai_api_key = self.openai_api_input.text()

        if not (upbit_access and upbit_secret and openai_api_key):
            self.log_text.append("Please enter all keys")
            return

        self.upbit = pyupbit.Upbit(upbit_access, upbit_secret)
        self.openai_client = OpenAI(api_key=openai_api_key)

        self.trading_thread = TradingThread(self.upbit, self.openai_client)
        self.trading_thread.update_signal.connect(self.update_log)
        self.trading_thread.update_chart.connect(self.update_chart)
        self.trading_thread.start()

        self.balance_thread = BalanceThread(self.upbit)
        self.balance_thread.update_balance.connect(self.update_balance)
        self.balance_thread.start()

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.log_text.append("Trading started")

    def stop_trading(self):
        if hasattr(self, 'trading_thread'):
            self.trading_thread.stop()
            self.trading_thread.wait()
        if hasattr(self, 'balance_thread'):
            self.balance_thread.stop()
            self.balance_thread.wait()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.log_text.append("Trading stopped")

    def update_log(self, message):
        self.log_text.append(message)

    def update_balance(self, balance_dict):
        if "error" in balance_dict:
            self.log_text.append(balance_dict["error"])
        else:
            self.krw_label.setText(f"KRW Balance: {balance_dict['krw']}")
            self.btc_label.setText(f"BTC Balance: {balance_dict['btc']}")
            self.price_label.setText(f"BTC Price: {balance_dict['price']}")

    def update_chart(self, df):
        plt.figure(figsize=(10, 6))
        
        # 데이터 준비
        df['Date'] = df.index
        df['Date'] = df['Date'].map(mdates.date2num)
        ohlc = df[['Date', 'open', 'high', 'low', 'close']].values

        # 캔들스틱 차트 그리기
        ax = plt.subplot()
        candlestick_ohlc(ax, ohlc, width=0.0005, colorup='red', colordown='blue')

        # x축 설정
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        plt.xticks(rotation=45)

        # 제목과 레이블 설정
        plt.title('BTC Price Chart', fontweight='bold')
        plt.xlabel('Time')
        plt.ylabel('Price (KRW)')

        plt.grid(True, linestyle='--', alpha=0.5)
        plt.tight_layout()

        # 이미지로 변환
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100)
        buffer.seek(0)
        image = QPixmap()
        image.loadFromData(buffer.getvalue())
        self.chart_label.setPixmap(image)
        plt.close()

    def update_chart_timer(self):
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute1", count=60)
        self.update_chart(df)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = TradingApp()
    ex.show()
    sys.exit(app.exec_())
