#!/bin/bash
# test_final.sh - Test simplificado del sistema corregido

echo "=== Test Final del Sistema Corregido ==="
echo

# Configuración
HOST="10.147.20.4"
FILE="data/video_test.mp4"

echo "1. Iniciando servidor..."
python3 server.py "$FILE" --host 0.0.0.0 --max-clients 10 &
SERVER_PID=$!

echo "Esperando 5 segundos para que el servidor inicie..."
sleep 5

if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo "❌ Error: Servidor no pudo iniciar"
    exit 1
fi

echo "✅ Servidor iniciado correctamente"
echo

echo "2. Test con 5 clientes simultáneos..."
for i in {1..5}; do 
    timeout 600 python3 client.py --host $HOST --output test_archivo_${i}.mp4 --client-id test_cliente_${i} --concurrent 3 &
    sleep 1
done

echo "Esperando que terminen los clientes (max 10 min)..."
wait

echo

echo "3. Verificando resultados..."
total_files=0
successful_files=0

for file in test_archivo_*.mp4; do
    if [ -f "$file" ]; then
        total_files=$((total_files + 1))
        size=$(stat -c%s "$file" 2>/dev/null || stat -f%z "$file" 2>/dev/null)
        
        if [ "$size" -eq 2241822426 ]; then
            successful_files=$((successful_files + 1))
            echo "✅ $file - OK"
        else
            echo "❌ $file - Size: ${size} bytes (expected: 2,241,822,426)"
        fi
    fi
done

echo "4. Deteniendo servidor..."
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null

echo
echo "📊 Resultados:"
echo "   - Archivos generados: $total_files/5"
echo "   - Descargas exitosas: $successful_files/5"

if [ $successful_files -eq 5 ]; then
    echo "🎉 PERFECTO: Todas las descargas exitosas"
elif [ $successful_files -gt 2 ]; then
    echo "✅ BUENO: Mayoría de descargas exitosas"
else
    echo "❌ PROBLEMAS: Pocas descargas exitosas"
fi
