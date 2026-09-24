# Политика конфиденциальности / Privacy Policy
**Продукт:** SunoSaver (Telegram-бот & Chrome Extension)  
**Дата последнего обновления:** 24 сентября 2026 г.  

---

## 🇷🇺 Русский язык

Настоящая Политика конфиденциальности (далее — «Политика») определяет порядок обработки и защиты персональной информации пользователей сервиса **SunoSaver** (Telegram-бот `@sunosaver_bot`, далее — «Сервис», «Бот»).

Мы уважаем ваше право на конфиденциальность и стремимся к максимальной прозрачности: бот собирает только минимально необходимый объём данных для обеспечения своей прямой функциональности и не использует ваши данные в коммерческих или рекламных целях.

---

### 1. Какие данные мы собираем
При взаимодействии с Ботом в локальной базе данных могут сохраняться следующие сведения:
1. **Идентификаторы Telegram:**
   - Ваш уникальный числовой идентификатор (`user_id`).
   - Имя пользователя (`@username`) и имя (`first_name`), если они указаны в профиле Telegram.
2. **Настройки интерфейса:**
   - Выбранный вами язык интерфейса (русский, английский или казахский).
   - Индивидуальная настройка имени автора аудиофайлов (если вы задали её командой `/artist`).
3. **Статистика использования и лимиты:**
   - Счётчики обращений (количество скачанных MP3, WAV, созданных миксов и стемов) для соблюдения правил честного использования (Fair Use) и защиты сервера от перегрузок.
   - История добавленных вами треков в личную библиотеку (для работы конструктора миксов).
4. **Реферальная программа и статус:**
   - Идентификатор пригласившего пользователя (если вы перешли по реферальной ссылке) и количество приглашённых вами друзей.
   - Наличие активного PRO-статуса.
   - Статус подписки на информационный канал проекта.
5. **Донаты (Telegram Stars):**
   - Факт совершения добровольной поддержки проекта (сумма в звёздах `XTR`, дата и `user_id` для отображения в Зале славы спонсоров).

---

### 2. Какие данные мы НЕ собираем
1. **Мы НЕ собираем конфиденциальные платёжные данные:** все платежи звёздами обрабатываются исключительно защищённой платформой Telegram. Бот не имеет доступа к вашим банковским картам, счетам или кошелькам.
2. **Мы НЕ запрашиваем учётные записи Suno:** вам не требуется вводить логины, пароли или токены от аккаунта Suno. Бот работает исключительно с публичными ссылками, отправленными в чат.
3. **Мы НЕ читаем личные сообщения:** Бот не имеет доступа к вашей переписке, контактам, фотогалерее или геолокации. Обрабатываются только сообщения и команды, отправленные непосредственно в диалог с Ботом.

---

### 3. Обработка аудио и временных файлов
1. **Мгновенное удаление:** временные файлы, создаваемые при конвертации аудио (FFmpeg) и разделении треков нейросетью (Demucs), обрабатываются в изолированных системных каталогах и **безвозвратно удаляются с сервера сразу после отправки файла пользователю**.
2. **Telegram Кэш:** для экономии интернет-трафика и мгновенной повторной отправки бот сохраняет только обезличенный внутренний идентификатор файла Telegram (`file_id`), название трека и публичный текст песни (lyrics).

---

### 4. Цели обработки данных
Собранные данные используются исключительно для:
- Предоставления услуг: скачивание треков, конвертация в WAV, разделение на вокал/минус, сборка миксов;
- Сохранения ваших персональных настроек (язык, теги автора);
- Защиты инфраструктуры проекта от злоупотреблений, DDoS-атак и спама;
- Учёта добровольной поддержки и начисления бонусных лимитов по реферальной системе.

---

### 5. Передача данных третьим лицам
- **Мы никогда не продаём, не сдаём в аренду и не передаём ваши данные третьим лицам**, рекламным сетям или маркетинговым агентствам.
- Взаимодействие происходит исключительно с:
  - **Telegram API** (инфраструктура мессенджера Telegram FZ-LLC);
  - **Публичными CDN-серверами Suno** — для загрузки общедоступных медиафайлов по ссылкам, переданным пользователем.

---

### 6. Право на удаление данных (Право на забвение)
Вы имеете полное право в любой момент запросить удаление всей информации о вашем аккаунте из базы данных бота (включая личную библиотеку и историю обращений).  
Для этого достаточно написать администратору проекта: **[@youtubestanmanager](https://t.me/youtubestanmanager)**. Данные будут безвозвратно удалены в течение 48 часов.

---

### 7. Контакты и обратная связь
Если у вас есть вопросы, предложения или претензии относительно настоящей Политики:
- **Telegram для связи:** [@youtubestanmanager](https://t.me/youtubestanmanager)
---

### 8. Браузерное расширение SunoSaver (Chrome Extension)
1. **Локальное хранение:** Расширение использует локальное хранилище браузера (`chrome.storage.local`) исключительно для сохранения счётчика скачиваний за текущий день (лимит 10 треков/день) и пользовательского лицензионного ключа PRO.
2. **Сетевые запросы:** Расширение связывается только с серверами Suno (`suno.com`, `suno.ai`, CDN `cloudfront.net`) для загрузки аудиопотока и с API платёжной системы Lemon Squeezy для валидации лицензии.
3. **Отсутствие трекинга:** Мы не собираем историю браузера, персональные данные, пароли, куки или телеметрию. Расширение активно только на страницах Suno.
4. **Разрешения (Permissions):** 
   - `storage`: локальное сохранение лимитов и ключа;
   - `downloads`: сохранение скачанных треков на диск пользователя;
   - `tabs` / `activeTab`: определение текущего трека на открытой вкладке Suno.

---
---

## 🇬🇧 English Version

This Privacy Policy governs the collection, use, and protection of information by the **SunoSaver** Telegram bot ([@sunosaver_bot](https://t.me/sunosaver_bot), hereinafter referred to as the "Service" or "Bot").

We respect your privacy and adhere to the principle of data minimization: the Bot only collects the minimum data necessary to operate its core features. We never monetize, sell, or advertise with your personal data.

---

### 1. Information We Collect
When you interact with the Bot, the following data may be stored in our database:
1. **Telegram Identifiers:**
   - Your unique numeric Telegram ID (`user_id`).
   - Your `@username` and `first_name` (if configured in your Telegram account).
2. **User Preferences:**
   - Your selected UI language (Russian, English, or Kazakh).
   - Custom ID3 artist tag preference (set via `/artist`).
3. **Usage Counters & Library:**
   - Request counters (MP3, WAV, mixes, stems) to enforce daily fair-use limits and prevent server overload.
   - Your saved track history in the personal library (used by the Suno Mix Maker feature).
4. **Referral & Status Information:**
   - Referrer ID (if you registered via an invite link) and the count of invited users.
   - PRO account status flag.
   - Required channel subscription confirmation.
5. **Donations (Telegram Stars):**
   - Records of voluntary Stars contributions (`amount`, `timestamp`, `user_id`) to display supporters in the Hall of Fame.

---

### 2. Information We DO NOT Collect
1. **No Financial or Payment Credentials:** All Stars donations are processed securely by Telegram. We never have access to your bank cards, account numbers, or billing info.
2. **No Suno Credentials:** You do not need to provide Suno logins, passwords, or cookies. The Bot only processes public URLs you submit.
3. **No Private Chats or Media Access:** The Bot cannot read messages from other chats, your contacts, photos, or location. It only receives messages sent directly to the Bot.

---

### 3. Audio Processing & Temporary Files
1. **Immediate Deletion:** Audio files processed locally on our server (via Demucs AI or FFmpeg) are placed in isolated temporary directories and **permanently deleted immediately after being sent to your chat**.
2. **Cloud Caching:** To avoid redundant network traffic, the Bot only stores Telegram `file_id` references and public song lyrics/titles.

---

### 4. How We Use Information
We use the collected information solely to:
- Deliver requested audio files, stems, WAV conversions, video cards, and lyrics;
- Maintain your personal settings and library;
- Protect the server against spam, abuse, and DDoS attacks;
- Calculate referral bonuses and PRO status privileges.

---

### 5. Third-Party Sharing
- **We never sell, rent, or trade your personal data to third parties, advertising brokers, or tracking networks.**
- External communications are limited strictly to:
  - **Telegram Bot API** (Telegram FZ-LLC);
  - **Suno Public CDN** — to fetch publicly accessible audio streams requested by the user.

---

### 6. Data Retention & Deletion (Right to be Forgotten)
You have the right to request full deletion of your user profile, track history, and related database records at any time.  
To request account deletion, contact our support team at **[@youtubestanmanager](https://t.me/youtubestanmanager)**. Your data will be permanently purged within 48 hours.

---

### 7. Contact Information
For inquiries, suggestions, or privacy concerns:
- **Support Contact:** [@youtubestanmanager](https://t.me/youtubestanmanager)
- **Official Channel:** [@youtubestantg](https://t.me/youtubestantg)

---

### 8. SunoSaver Chrome Extension
1. **Local Storage:** The extension uses `chrome.storage.local` solely to track daily free download counts (10 tracks/day limit) and the user's PRO license key.
2. **Network Requests:** The extension only connects to Suno (`suno.com`, `suno.ai`, CDN `cloudfront.net`) to stream audio files, and Lemon Squeezy's official API to validate PRO licenses.
3. **No Tracking or Spyware:** We do not collect, track, or transmit your browser history, personal identity, passwords, or cookies. The extension only runs on Suno web pages.
4. **Permissions Justification:**
   - `storage`: saves download limits and license key locally on your device.
   - `downloads`: saves converted MP3/WAV files to your machine.
   - `tabs` / `activeTab`: detects the current song on the active Suno tab.
