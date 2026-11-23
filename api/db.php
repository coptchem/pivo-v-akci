<?php
// Sdílený kód API odběrů: SQLite databáze, e-maily, HTML stránky.
declare(strict_types=1);

// varování (např. z mail()) nesmí kontaminovat JSON/HTML odpovědi
ini_set('display_errors', '0');

// config.php generuje deploy z GitHub secrets (EXPORT_TOKEN, SITE_URL,
// MAIL_FROM); není v repozitáři – viz api/config.example.php
if (file_exists(__DIR__ . '/config.php')) {
    require __DIR__ . '/config.php';
}
if (!defined('SITE_URL')) {
    define('SITE_URL', 'https://' . preg_replace('/[^a-z0-9.\-:]/i', '', $_SERVER['HTTP_HOST'] ?? 'localhost'));
}
if (!defined('MAIL_FROM')) {
    define('MAIL_FROM', 'noreply@' . (parse_url(SITE_URL, PHP_URL_HOST) ?: 'localhost'));
}

const DB_PATH = __DIR__ . '/data/subscribers.sqlite';
const KNOWN_CHAINS = ['Kaufland', 'Lidl', 'Penny', 'Albert', 'Billa', 'Tesco', 'Globus', 'COOP'];
const TOKEN_RE = '/^[0-9a-f]{48}$/';
const PENDING_TTL_S = 7 * 86400;     // nepotvrzené přihlášky/změny se po týdnu mažou
const MAIL_COOLDOWN_S = 600;         // max 1 potvrzovací e-mail na adresu za 10 min

function db(): PDO
{
    static $pdo = null;
    if ($pdo !== null) {
        return $pdo;
    }
    if (!is_dir(__DIR__ . '/data')) {
        mkdir(__DIR__ . '/data', 0750, true);
    }
    $pdo = new PDO('sqlite:' . DB_PATH);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('PRAGMA busy_timeout = 5000');
    $pdo->exec('CREATE TABLE IF NOT EXISTS subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        chains TEXT NOT NULL DEFAULT "[]",
        brands TEXT NOT NULL DEFAULT "[]",
        token TEXT NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        confirmed_at TEXT
    )');
    $pdo->exec('CREATE TABLE IF NOT EXISTS rate_limit (
        ip TEXT NOT NULL,
        ts INTEGER NOT NULL
    )');

    // migrace: čekající změna nastavení u potvrzeného odběru (nesmí odběr
    // vypnout, dokud ji majitel adresy nepotvrdí) + omezení e-mailů na adresu
    $cols = array_column($pdo->query('PRAGMA table_info(subscribers)')->fetchAll(PDO::FETCH_ASSOC), 'name');
    $add = [
        'pending_chains' => 'TEXT',
        'pending_brands' => 'TEXT',
        'pending_token' => 'TEXT',
        'pending_at' => 'INTEGER',
        'last_mail_at' => 'INTEGER',
    ];
    foreach ($add as $col => $type) {
        if (!in_array($col, $cols, true)) {
            $pdo->exec("ALTER TABLE subscribers ADD COLUMN $col $type");
        }
    }
    cleanup($pdo);
    return $pdo;
}

function cleanup(PDO $pdo): void
{
    $limit = time() - PENDING_TTL_S;
    // nepotvrzené přihlášky starší než týden se mažou (slib z potvrzovacího e-mailu)
    $pdo->prepare('DELETE FROM subscribers WHERE confirmed = 0 AND created_at < ?')
        ->execute([gmdate('c', $limit)]);
    $pdo->prepare('UPDATE subscribers SET pending_chains = NULL, pending_brands = NULL,
            pending_token = NULL, pending_at = NULL
        WHERE pending_at IS NOT NULL AND pending_at < ?')
        ->execute([$limit]);
    $pdo->prepare('DELETE FROM rate_limit WHERE ts < ?')->execute([time() - 3600]);
}

function new_token(): string
{
    return bin2hex(random_bytes(24));
}

function send_mail(string $to, string $subject, string $body): bool
{
    $headers = 'From: Pivo v akci <' . MAIL_FROM . ">\r\n"
        . "MIME-Version: 1.0\r\n"
        . "Content-Type: text/plain; charset=UTF-8\r\n"
        . 'Content-Transfer-Encoding: 8bit';
    $encSubject = '=?UTF-8?B?' . base64_encode($subject) . '?=';
    return @mail($to, $encSubject, $body, $headers, '-f' . MAIL_FROM);
}

function json_response(int $code, array $data): void
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

/** Jednoduchá HTML stránka; $extraHtml se nevkládá escapovaný (jen vlastní kód). */
function html_page(string $title, string $message, string $extraHtml = ''): void
{
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Frame-Options: DENY');
    header('Referrer-Policy: no-referrer');
    $t = htmlspecialchars($title, ENT_QUOTES);
    $m = htmlspecialchars($message, ENT_QUOTES);
    $home = htmlspecialchars(SITE_URL, ENT_QUOTES);
    echo "<!doctype html><html lang=\"cs\"><head><meta charset=\"utf-8\">"
        . '<meta name="viewport" content="width=device-width, initial-scale=1">'
        . '<meta name="robots" content="noindex">'
        . "<title>$t – Pivo v akci</title>"
        . '<style>body{font-family:system-ui,sans-serif;background:#f6f4ef;color:#1f1c17;'
        . 'display:flex;justify-content:center;padding:15vh 16px 0;text-align:center}'
        . 'a{color:#8a6408}button{font:inherit;font-weight:600;border:0;border-radius:10px;'
        . 'padding:10px 18px;background:#2b2620;color:#fff;cursor:pointer}</style></head><body><div>'
        . "<h1>🍺 $t</h1><p>$m</p>$extraHtml"
        . '<p><a href="' . $home . '">Zpět na Pivo v akci</a></p>'
        . '</div></body></html>';
    exit;
}
