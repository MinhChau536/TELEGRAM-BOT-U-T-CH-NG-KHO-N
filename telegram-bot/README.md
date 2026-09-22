# Telegram Stock Signal Bot

This project is a Telegram bot designed to provide stock market signals and analysis. It fetches stock data and financial metrics, allowing users to receive updates and insights on various stocks.

## Project Structure

- **Bot.py**: Contains the main logic for the Telegram bot, handling commands and user interactions.
- **DNSE.py**: Responsible for fetching stock prices and financial data.
- **subscribers.json**: Stores user IDs of subscribers for updates.
- **.env**: Contains environment variables, including the Telegram bot token.
- **requirements.txt**: Lists the required Python packages for the project.
- **README.md**: Documentation for the project.

## Features

- **Stock Information**: Users can check stock prices and receive detailed analysis.
- **Chart Generation**: The bot can generate charts for stock performance.
- **Subscription Management**: Users can subscribe to receive updates on specific stocks.

## Setup Instructions

1. **Clone the Repository**:
   ```
   git clone <repository-url>
   cd telegram-bot
   ```

2. **Install Dependencies**:
   Make sure you have Python 3.10 or higher installed. Then, install the required packages:
   ```
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables**:
   Create a `.env` file in the project root and add your Telegram bot token:
   ```
   TELEGRAM_BOT_TOKEN="<your-telegram-bot-token>"
   ```

4. **Run the Bot**:
   Execute the following command to start the bot:
   ```
   python Bot.py
   ```

## Usage

- Start the bot by sending the `/start` command.
- Use `/check <stock-symbol>` to get the latest stock analysis.
- Use `/subscribe <stock-symbol>` to subscribe for updates on a specific stock.
- Use `/unsubscribe <stock-symbol>` to stop receiving updates.

## Contributing

Feel free to submit issues or pull requests to improve the bot's functionality or documentation.