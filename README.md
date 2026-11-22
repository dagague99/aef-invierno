# Foto de invierno afinada · ANÁLISIS EN FRÍO

Calcula y publica las teselas de la «Foto de invierno afinada» del Pirineo que usa Mapas en frío (analisisenfrio.com).

- `cobertura.json`: qué se cubre (Pirineo por encima de unos 1.500 m, con margen), en 59 bloques de teselas de nivel 14 (1.024 puntos).
- `bloque.py`: hace un bloque entero (elige el día, baja los datos, afina con SEN2SR, mezcla con el relieve y la foto de verano, saca las teselas).
- `piramide.py`: junta los bloques, hace los niveles de lejos y prepara la web.
- `.github/workflows/invierno.yml`: lo lanza todo en GitHub (pestaña Actions › «Foto de invierno afinada» › Run workflow) y lo publica en GitHub Pages.

Las teselas quedan en `https://<usuario>.github.io/aef-invierno/teselas/{z}-{x}-{y}.webp` (niveles 9 a 14).

Datos: Copernicus Sentinel-2 (vía Microsoft Planetary Computer), relieve Mapterhorn, ortofotos PNOA (IGN España, CC BY 4.0) e IGN France. Modelo: SEN2SR (tacofoundation).
