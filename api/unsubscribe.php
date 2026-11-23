<?php
// Odhlášení odběru – záznam se smaže.
// GET zobrazí potvrzovací tlačítko (antivirové skenery pošty otevírají odkazy
// a nesmí tím nikoho odhlásit), POST odhlásí. POST podporuje i one-click
// odhlášení z poštovních klientů (RFC 8058, hlavička List-Unsubscribe-Post).
declare(strict_types=1);
require __DIR__ . '/db.php';

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$token = (string)($method === 'POST' ? ($_POST['t'] ?? $_GET['t'] ?? '') : ($_GET['t'] ?? ''));
if (!preg_match(TOKEN_RE, $token)) {
    html_page('Neplatný odkaz', 'Odkaz je neúplný nebo poškozený.');
}

if ($method !== 'POST') {
    $t = htmlspecialchars($token, ENT_QUOTES);
    html_page('Odhlásit odběr?', 'Po odhlášení vaši e-mailovou adresu smažeme a žádná další upozornění nepřijdou.',
        '<form method="post" action="unsubscribe.php"><input type="hidden" name="t" value="' . $t . '">'
        . '<button type="submit">Ano, odhlásit</button></form>');
}

$stmt = db()->prepare('DELETE FROM subscribers WHERE token = ?');
$stmt->execute([$token]);

if ($stmt->rowCount() > 0) {
    html_page('Odhlášeno', 'Váš e-mail jsme smazali, žádná další upozornění nepřijdou.');
}
html_page('Odhlášeno', 'Tento odběr už neexistuje – nic dalšího nepřijde.');
