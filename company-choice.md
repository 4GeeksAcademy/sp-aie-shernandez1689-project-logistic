He elegido TrackFlow porque me interesa la logística y el reto de construir tecnología para coordinar operaciones físicas a escala, en dos países y con sistemas distintos.

## Departamentos que más me interesan

**Última milla y gestión de transportistas.** Me atrae integrar los datos de ocho transportistas con APIs y formatos diferentes para ofrecer un seguimiento unificado. También sería interesante desarrollar un motor que recomiende el transportista más adecuado según destino, peso, urgencia, coste y rendimiento histórico, explicando por qué recomienda esa opción.

**Logística inversa.** Las devoluciones tienen un impacto operativo y económico importante, y hoy cada caso depende de una revisión manual. Aquí se pueden combinar reglas configurables por cliente, automatización de procesos y visión artificial para clasificar el estado de los productos. Me interesa especialmente diseñar el sistema para que derive a una persona los casos dudosos, en vez de automatizar decisiones sin suficiente confianza.

## Reto que quiero construir

Del milestone map, el reto que más ganas tengo de desarrollar es una **automatización del flujo de devoluciones**, alineada con el hito 9 (Workflows). El flujo recibiría una solicitud, aplicaría las reglas del cliente y, si se aprueba, generaría la etiqueta, enviaría instrucciones al consumidor y programaría la recogida con un transportista. Como apoyo, una IA podría clasificar fotos del producto devuelto y recomendar reacondicionarlo, devolverlo al inventario o descartarlo; los casos de baja confianza pasarían a revisión humana.

Me interesa porque conecta una necesidad real con varias piezas técnicas —reglas de negocio, integraciones, IA y seguimiento del proceso— y permite medir resultados concretos, como el tiempo de resolución, el porcentaje de decisiones automáticas y la consistencia de las inspecciones.

## Mi idea de Agente de IA

El agente recibiría la solicitud de devolución y consultaría los datos del pedido, las reglas de devolución del cliente y, cuando el producto llegue al almacén, las fotos de su inspección. Con esa información recomendaría aprobar o rechazar la devolución y clasificar el producto para reacondicionarlo, devolverlo al inventario o descartarlo. Si la decisión cumple las reglas y la confianza es suficiente, iniciaría el siguiente paso: generar la etiqueta, enviar instrucciones y programar la recogida o el procesamiento del producto. Si faltan datos, las reglas no son claras o la imagen no permite una clasificación fiable, enviaría el caso a una persona para su revisión.