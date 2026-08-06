# Відновлення production

## Принцип

`main` - єдине джерело правди для production. Якщо деплой зламаний, не
переписуй історію через `git reset --hard` або force push. Створи новий коміт,
який скасовує небезпечну зміну.

## Передумови

- GitHub Actions `Preflight` має бути required check для pull request у `main`.
- У Render для Web Service має бути увімкнено auto-deploy з гілки `main`.
- Після кожного деплою перевіряй `/healthz` і основну сторінку застосунку.

## Rollback одного коміту

1. У Render або GitHub визнач SHA останнього невдалого коміту та останнього
   стабільного коміту.
2. Онови локальний `main`:

   ```bash
   git switch main
   git pull --ff-only origin main
   git log --oneline
   ```

3. Створи коміт-відкат і відправ його у GitHub:

   ```bash
   git revert <bad-commit-sha>
   git push origin main
   ```

4. Дочекайся успішного `Preflight`, після чого Render задеплоїть новий коміт у
   `main`.
5. Перевір `https://<service-name>.onrender.com/healthz` і ключовий сценарій у
   браузері.

Якщо потрібно скасувати merge commit, використовуй
`git revert -m 1 <merge-commit-sha>`. Це залишає історію зрозумілою і дозволяє
повторно перевірити відкат через CI.
