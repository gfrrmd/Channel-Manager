# Channel Manager Bot (Telegram, Bot API 10.3)

Bot untuk mengelola channel: kirim konten apa saja ke bot, tambahkan **button di dalam teks**
(Rich Message / `RichMessageButton`), lalu post ke channel sekarang atau terjadwal.

## Fitur
- Terima teks, foto, video, animasi, audio, dokumen (rich) + voice, sticker, poll, dll. (mode klasik)
- Button di dalam teks via `sendRichMessage` + blok `buttons`, dengan style `primary/success/danger`
- Fallback otomatis ke inline keyboard biasa bila rich message ditolak (`RICH_MODE=auto`)
- Hubungkan channel dengan tombol **request_chat** (Telegram menawarkan menambahkan bot sebagai admin),
  forward post, atau `@username`; auto-link saat bot dijadikan admin
- Cek izin bot (post/edit/delete) dan status admin pengguna
- Pilih channel tujuan, Silent, Protect content, Pin
- Jadwal posting (persisten di PostgreSQL, aman terhadap redeploy), pembatalan jadwal
- Riwayat, edit button post yang sudah terkirim, hapus post dari channel
- Button **vote** dengan counter (satu vote per user per button, bisa dibatalkan)
- Whitelist pengguna (`ALLOWED_USERS`), zona waktu per pengguna (`/tz`)

## Sintaks button
```
Website - https://example.com
Chat - @username | Docs - docs.example.com
Beli - https://shop.id - primary
👍 Suka - vote
```
Baris = baris button, `|` memisahkan button dalam satu baris, style opsional di akhir.

## Deploy ke Railway
1. Push folder ini ke GitHub, lalu **New Project → Deploy from GitHub repo**.
2. Tambahkan **PostgreSQL** (New → Database → PostgreSQL).
3. Di service bot, buka **Variables**:
   - `BOT_TOKEN` = token dari @BotFather
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`
   - `ALLOWED_USERS` = user id kamu (opsional tapi disarankan)
   - `DEFAULT_TZ` = `Asia/Jakarta`
4. Deploy. Bot memakai long polling, jadi tidak perlu domain/port.

## Jalan lokal
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # isi, lalu export variabelnya
python -m bot.main
```

## Catatan penting
- Rich Messages memakai Bot API 10.3 (24 Agustus 2026). Klien Telegram yang terlalu lama
  mungkin tidak merender rich message dengan benar; tes dulu dengan akun subscriber.
- Button **URL** paling aman di channel. Button **vote** (callback) bergantung pada Telegram
  mengirim `callback_query` untuk rich message di channel; tes dengan satu post sebelum dipakai luas.
- State percakapan disimpan di memori: draft yang belum diposting hilang saat redeploy
  (post terjadwal tersimpan di database, jadi aman).
- Album (media group) belum didukung; kirim satu media per post.
