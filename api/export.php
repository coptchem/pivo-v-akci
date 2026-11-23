<?php
// Export potvrzených odběratelů pro rozesílací skript v GitHub Actions.
// Chráněno tajným tokenem v hlavičce X-Export-Token (EXPORT_TOKEN
// v config.php, generuje ho deploy). Token se neposílá v URL, aby
// nekončil v access logu hostingu.
declare(strict_types=1);
require __DIR__ . '/db.php';

if (!defined('EXPORT_TOKEN') || strlen((string)EXPORT_TOKEN) < 32) {
    json_response(503, ['ok' => false, 'error' => 'Export není nakonfigurován.']);
}
$given = (string)($_SERVER['HTTP_X_EXPORT_TOKEN'] ?? '');
if (!hash_equals((string)EXPORT_TOKEN, $given)) {
    usleep(300000);
    json_response(403, ['ok' => false, 'error' => 'Neplatný token.']);
}

$rows = db()->query(
    'SELECT email, chains, brands, token FROM subscribers WHERE confirmed = 1')
    ->fetchAll(PDO::FETCH_ASSOC);

$subscribers = array_map(fn($r) => [
    'email' => $r['email'],
    'chains' => json_decode($r['chains'], true) ?: [],
    'brands' => json_decode($r['brands'], true) ?: [],
    'token' => $r['token'],
], $rows);

json_response(200, ['ok' => true, 'subscribers' => $subscribers]);
