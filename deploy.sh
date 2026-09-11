#!/bin/bash
# deploy.sh — быстрый деплой SunoSaver на Oracle Cloud
# Использование: ./deploy.sh ВАШ_IP

SERVER_IP="${1:-130.61.185.173}"
KEY_FILE=$(ls ~/Downloads/ssh-key-*.key 2>/dev/null | head -1)
BOT_DIR="/Users/abaylaa/Desktop/sunosaver"
REMOTE_DIR="ubuntu@${SERVER_IP}:~/sunosaver/"

if [ -z "$KEY_FILE" ]; then
    echo "❌ SSH ключ не найден в ~/Downloads/"
    exit 1
fi

echo "📤 Загружаю файлы на ${SERVER_IP}..."
scp -i "$KEY_FILE" \
    "$BOT_DIR/bot.py" \
    "$BOT_DIR/database.py" \
    "$BOT_DIR/requirements.txt" \
    "$BOT_DIR/.env" \
    "$REMOTE_DIR"

if [ $? -eq 0 ]; then
    echo "🔄 Перезапускаю бота..."
    ssh -i "$KEY_FILE" "ubuntu@${SERVER_IP}" "sudo systemctl restart sunosaver"
    echo "✅ Готово! Проверь статус:"
    echo "   ssh -i $KEY_FILE ubuntu@${SERVER_IP} 'sudo systemctl status sunosaver'"
else
    echo "❌ Ошибка при загрузке файлов"
    exit 1
fi
