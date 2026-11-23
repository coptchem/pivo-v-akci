<?php
// Přihlášení k odběru upozornění: POST JSON {email, chains[], brands[], web}.
// "web" je honeypot proti botům – lidé ho nechají prázdný.
// Double opt-in: nový odběr i změna nastavení čekají na potvrzení odkazem
// z e-mailu. Odpověď je pro existující i novou adresu stejná (neprozrazuje,
// kdo odebírá).
declare(strict_types=1);
require __DIR__ . '/db.php';

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    json_response(405, ['ok' => false, 'error' => 'Pouze POST.']);
}
if (stripos($_SERVER['CONTENT_TYPE'] ?? '', 'application/json') !== 0) {
    // JSON content-type vynutí CORS preflight -> cizí weby formulář neodešlou
    json_response(415, ['ok' => false, 'error' => 'Očekávám JSON.']);
}

$raw = file_get_contents('php://input', false, null, 0, 20000) ?: '';
$input = json_decode($raw, true);
if (!is_array($input)) {
    json_response(400, ['ok' => false, 'error' => 'Neplatný požadavek.']);
}

$okMessage = 'Hotovo! Do e-mailu jsme poslali potvrzovací odkaz.';

if (!empty($input['web'])) {
    // honeypot vyplněn -> bot; tváříme se, že vše prošlo
    json_response(200, ['ok' => true, 'message' => $okMessage]);
}

$email = strtolower(trim((string)($input['email'] ?? '')));
if (strlen($email) > 200 || !filter_var($email, FILTER_VALIDATE_EMAIL)) {
    json_response(400, ['ok' => false, 'error' => 'Zadejte platný e-mail.']);
}

$chains = array_values(array_intersect(
    KNOWN_CHAINS, array_map('strval', (array)($input['chains'] ?? []))));
$brands = array_values(array_unique(array_filter(
    array_map(fn($b) => trim(strip_tags((string)$b)), (array)($input['brands'] ?? [])),
    fn($b) => $b !== '' && strlen($b) <= 120)));
if (count($brands) > 60) {
    json_response(400, ['ok' => false, 'error' => 'Příliš mnoho značek.']);
}

$pdo = db();

// rate limit: max 5 požadavků za hodinu z jedné IP
$ip = $_SERVER['REMOTE_ADDR'] ?? '?';
$now = time();
$stmt = $pdo->prepare('SELECT COUNT(*) FROM rate_limit WHERE ip = ?');
$stmt->execute([$ip]);
if ((int)$stmt->fetchColumn() >= 5) {
    json_response(429, ['ok' => false, 'error' => 'Příliš mnoho pokusů, zkuste to za hodinu.']);
}
$pdo->prepare('INSERT INTO rate_limit (ip, ts) VALUES (?, ?)')->execute([$ip, $now]);

$stmt = $pdo->prepare('SELECT * FROM subscribers WHERE email = ?');
$stmt->execute([$email]);
$row = $stmt->fetch(PDO::FETCH_ASSOC);

// ochrana schránky před zahlcením: max 1 potvrzovací e-mail za 10 minut
if ($row && $row['last_mail_at'] && $now - (int)$row['last_mail_at'] < MAIL_COOLDOWN_S) {
    json_response(200, ['ok' => true, 'message' => $okMessage]);
}

$token = new_token();
$chainsJson = json_encode($chains, JSON_UNESCAPED_UNICODE);
$brandsJson = json_encode($brands, JSON_UNESCAPED_UNICODE);

if ($row && (int)$row['confirmed']) {
    // potvrzený odběr: nové nastavení jen uložíme jako čekající, stávající
    // odběr běží dál, dokud změnu majitel adresy nepotvrdí
    $pdo->prepare('UPDATE subscribers SET pending_chains = ?, pending_brands = ?,
            pending_token = ?, pending_at = ?, last_mail_at = ? WHERE id = ?')
        ->execute([$chainsJson, $brandsJson, $token, $now, $now, $row['id']]);
    $intro = "na webu Pivo v akci bylo zadáno nové nastavení vašeho odběru:\n\n";
    $action = 'Změnu potvrdíte kliknutím na tento odkaz';
    $ignore = "Pokud jste o změnu nežádali, e-mail ignorujte – odběr zůstane beze změny.\n\n";
    $subject = 'Potvrďte změnu odběru – Pivo v akci';
} else {
    $pdo->prepare('INSERT INTO subscribers (email, chains, brands, token, confirmed, created_at, last_mail_at)
        VALUES (?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            chains = excluded.chains, brands = excluded.brands, token = excluded.token,
            created_at = excluded.created_at, last_mail_at = excluded.last_mail_at')
        ->execute([$email, $chainsJson, $brandsJson, $token, gmdate('c', $now), $now]);
    $intro = "na webu Pivo v akci bylo zadáno přihlášení k odběru upozornění\n"
        . "na akční ceny piva s tímto nastavením:\n\n";
    $action = 'Odběr potvrdíte kliknutím na tento odkaz';
    $ignore = "Pokud jste o odběr nežádali, e-mail ignorujte – bez potvrzení\n"
        . "nebudeme nic posílat a adresa bude do týdne smazána.\n\n";
    $subject = 'Potvrďte odběr upozornění – Pivo v akci';
}

$confirmUrl = SITE_URL . '/api/confirm.php?t=' . $token;
$chainsTxt = $chains ? implode(', ', $chains) : 'všechny řetězce';
$brandsTxt = $brands ? implode(', ', $brands) : 'všechny značky';
$body = "Dobrý den,\n\n" . $intro
    . "  Řetězce: $chainsTxt\n"
    . "  Značky:  $brandsTxt\n\n"
    . "$action (platí 7 dní):\n\n"
    . "  $confirmUrl\n\n"
    . $ignore
    . "Pivo v akci\n" . SITE_URL . "\n";

if (!send_mail($email, $subject, $body)) {
    json_response(500, ['ok' => false, 'error' => 'Potvrzovací e-mail se nepodařilo odeslat, zkuste to později.']);
}

json_response(200, ['ok' => true, 'message' => $okMessage]);
