# Cobertura de Transportistas — TrackFlow

## Transportistas contratados

TrackFlow opera con los siguientes transportistas según la zona geográfica:

### España

| Transportista | Zona de cobertura | Tipo de servicio |
|---|---|---|
| Correos | Nacional (peninsular + islas) | Estándar y certificado |
| SEUR | Peninsular | Express y same-day |
| MRW | Peninsular + Canarias | Estándar y express |
| DHL Spain | Peninsular | Internacional y express |

### Estados Unidos

| Transportista | Zona de cobertura | Tipo de servicio |
|---|---|---|
| UPS | Continental US | Estándar y express |
| FedEx | Continental US + Alaska | Estándar y express |
| USPS | Continental US | Estándar (económico) |
| DHL Express | Internacional | Express internacional |

## Cobertura rural

### Aragón rural

Para zonas rurales de Aragón (Teruel, Huesca interior, Zaragoza provincia),
el transportista con mejor cobertura es **MRW**, que llega al 95% de los
códigos postales de la zona con plazo estándar de 2–3 días laborables.

SEUR no cubre el 30% de las zonas rurales aragonesas. Para estas zonas,
TrackFlow recomienda usar MRW como transportista principal.

### Zonas rurales de EE. UU.

En Estados Unidos, **USPS** ofrece la cobertura rural más amplia gracias a
su red de oficinas postales. UPS y FedEx pueden tener restricciones en
zonas de baja densidad de población.

## Requisitos de integración

Todos los transportistas ofrecen integración por API para:
- Generación de etiquetas automatizada.
- Tracking en tiempo real.
- Confirmación de entrega.

TrackFlow mantiene un conector unificado que abstrae las diferencias entre
transportistas. La incorporación de un nuevo transportista requiere
2–4 semanas de integración técnica.