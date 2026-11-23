<?php
// Potvrzení odběru nebo změny nastavení odkazem z e-mailu (double opt-in).
declare(strict_types=1);
require __DIR__ . '/db.php';

$token = (string)($_GET['t'] ?? '');
if (!preg_match(TOKEN_RE, $token)) {
    html_page('Neplatný odkaz', 'Odkaz je neúplný nebo poškozený.');
}

$pdo = db();

// 1) potvrzení nového odběru
$stmt = $pdo->prepare('SELECT id, confirmed FROM subscribers WHERE token = ?');
$stmt->execute([$token]);
$row = $stmt->fetch(PDO::FETCH_ASSOC);
if ($row) {
    if (!(int)$row['confirmed']) {
        $pdo->prepare('UPDATE subscribers SET confirmed = 1, confirmed_at = ? WHERE id = ?')
            ->execute([gmdate('c'), $row['id']]);
    }
    html_page('Odběr potvrzen', 'Až se objeví nové akce podle vašeho výběru, pošleme vám je e-mailem. Odhlásit se můžete odkazem v každém e-mailu.');
}

// 2) potvrzení změny nastavení u existujícího odběru
$stmt = $pdo->prepare('SELECT id, pending_chains, pending_brands FROM subscribers WHERE pending_token = ?');
$stmt->execute([$token]);
$row = $stmt->fetch(PDO::FETCH_ASSOC);
if ($row) {
    $pdo->prepare('UPDATE subscribers SET chains = ?, brands = ?, pending_chains = NULL,
            pending_brands = NULL, pending_token = NULL, pending_at = NULL WHERE id = ?')
        ->execute([$row['pending_chains'], $row['pending_brands'], $row['id']]);
    html_page('Nastavení uloženo', 'Upozornění vám budou chodit podle nového výběru.');
}

html_page('Odkaz nenalezen', 'Odkaz už neplatí (platnost je 7 dní) – přihlaste se prosím znovu na webu.');
