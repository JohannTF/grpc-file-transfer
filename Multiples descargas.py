import subprocess
import os
import time

NUM_CLIENTES = 50  # Cambia según lo que necesites

def lanzar_descargas():
    procesos = []

    # Ruta al python.exe del entorno virtual
    python_env = os.path.join(os.environ['VIRTUAL_ENV'], 'Scripts', 'python.exe')

    for i in range(NUM_CLIENTES):
        nombre_archivo = f"mi_video_descargado_{i+1}.mp4"

        proceso = subprocess.Popen(
            [python_env, "client.py", "--output", nombre_archivo],
            env={"PYTHONPATH": ".", **os.environ},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        print(f"[INFO] Lanzado cliente {i+1} → {nombre_archivo}")
        procesos.append((i + 1, proceso))

    for i, proceso in procesos:
        stdout, stderr = proceso.communicate()
        print(f"\n[CLIENTE {i}] Salida estándar:\n{stdout}")
        if stderr:
            print(f"[CLIENTE {i}] Errores:\n{stderr}")

if __name__ == "__main__":
    start = time.time()
    lanzar_descargas()
    end = time.time()
    print(f"\n✅ Todas las descargas finalizadas en {end - start:.2f} segundos.")
