"""Obtiene el refresh token de YouTube. Se ejecuta UNA sola vez, en tu equipo.

Antes de lanzarlo, en https://console.cloud.google.com:

  1. Crea un proyecto (o usa uno existente).
  2. APIs y servicios → Biblioteca → habilita «YouTube Data API v3».
  3. APIs y servicios → Pantalla de consentimiento OAuth → tipo «Externo».
     Rellena nombre y correo. En «Usuarios de prueba» añade la cuenta de Google
     dueña del canal Éter. No hace falta publicar la app ni pasar verificación.
  4. Credenciales → Crear credenciales → ID de cliente de OAuth →
     tipo «Aplicación de escritorio». Descarga el JSON.

Después:

    pip install google-auth-oauthlib
    python scripts/get_youtube_token.py ruta/al/client_secret.json

Se abrirá el navegador, inicias sesión con la cuenta del canal y aceptas. El
script imprime los tres valores que hay que guardar como GitHub Secrets:
YT_CLIENT_ID, YT_CLIENT_SECRET y YT_REFRESH_TOKEN.

Nota: mientras la app siga en modo «Prueba», el refresh token caduca a los 7
días. Para que dure de forma indefinida, pulsa «Publicar aplicación» en la
pantalla de consentimiento. Google mostrará un aviso de app no verificada al
autorizar: es esperado, ya que la app es tuya y solo la usas tú.
"""

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    secret = Path(sys.argv[1])
    if not secret.exists():
        print(f"No existe: {secret}")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    data = json.loads(secret.read_text(encoding="utf-8"))
    installed = data.get("installed") or data.get("web") or {}

    print("\n" + "=" * 68)
    print("Guarda estos tres valores en GitHub → Settings → Secrets and")
    print("variables → Actions → New repository secret:\n")
    print(f"YT_CLIENT_ID      {installed.get('client_id', '')}")
    print(f"YT_CLIENT_SECRET  {installed.get('client_secret', '')}")
    print(f"YT_REFRESH_TOKEN  {creds.refresh_token}")
    print("=" * 68)
    print("\nNo guardes esto en ningún fichero del repositorio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
