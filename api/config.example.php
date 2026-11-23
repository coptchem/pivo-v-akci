<?php
// Vzor konfigurace. Skutečný api/config.php generuje deploy workflow
// z GitHub secrets – do repozitáře ho nikdy necommitujte.
const EXPORT_TOKEN = 'nahodny-retezec-alespon-32-znaku';   // openssl rand -hex 32
const SITE_URL = 'https://example.com';
const MAIL_FROM = 'upozorneni@example.com';
