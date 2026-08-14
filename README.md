# Punto Brazacorta

Marcador móvil en directo para el torneo de voleibol de Brazacorta. La aplicación está construida con Flask, SQLite, JavaScript vanilla y una PWA ligera.

## Arrancar en local

```bash
cp .env.example .env
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Después abre `http://localhost:5000`.

El panel privado está en `http://localhost:5000/acceso`. Las cuentas iniciales son:

| Usuario | Contraseña inicial |
| --- | --- |
| `arbitro1` | `saque1` |
| `arbitro2` | `saque2` |
| `arbitro3` | `saque3` |
| `arbitro4` | `saque4` |
| `arbitro5` | `saque5` |

Define las contraseñas en `.env` antes del primer arranque. Se guardan como hash en SQLite y no se vuelven a cambiar automáticamente si el usuario ya existe.

## Producción

Para mantener las conexiones SSE abiertas se usa un solo proceso Gunicorn con varios hilos:

```bash
.venv/bin/gunicorn --worker-class gthread --threads 8 --workers 1 --bind 0.0.0.0:5000 app:app
```

Ese puerto se puede publicar con Cloudflare Tunnel. El bus de eventos es local al proceso, por eso no hay que usar varios workers sin añadir Redis u otro sistema de eventos compartido.

## Flujo de uso

1. Entra como árbitro desde el icono de candado del pie de página.
2. Crea el torneo, escribe un equipo por línea y revisa las reglas de cada ronda.
3. Si el torneo ya ha empezado, selecciona el partido y usa “Cargar marcador”.
4. Selecciona un partido pendiente, pulsa “Empezar partido” y suma puntos con los dos botones grandes.
5. El público ve los cambios en `/` y el bracket actualizado en `/cuadro`.

El sistema crea brackets hasta la siguiente potencia de dos y asigna pases automáticos cuando faltan equipos. Los puntos se guardan con el set y el tiempo transcurrido. Si el móvil se queda sin conexión, los puntos se almacenan en IndexedDB y se envían al recuperar la conexión.

## Pruebas

```bash
.venv/bin/python -m unittest discover -s tests -v
```
