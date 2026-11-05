# SPF, DKIM y DMARC — qué son y cómo configurarlos

**Idea central:** cualquiera puede escribir `From: ventas@tuempresa.com` en un correo falso. SPF, DKIM y DMARC son tres capas con las que el servidor receptor (Gmail, Outlook…) comprueba que el correo **de verdad viene de quien dice** y decide qué hacer si no. Sin ellas, tus correos de ventas tienden a caer en spam o a ser rechazados.

> El demo **no** usa estas configuraciones (envía a archivos). Esto explica cómo se hace en la realidad.

## La analogía

| Pieza | Pregunta que responde | Analogía |
|---|---|---|
| **SPF** | ¿Este servidor está autorizado a enviar por este dominio? | La lista de repartidores autorizados de la empresa |
| **DKIM** | ¿El mensaje fue firmado por el dominio y no se alteró en el camino? | Un sello de cera en el sobre |
| **DMARC** | ¿Qué hago si SPF/DKIM fallan, y a quién le aviso? | La política de la empresa: "si el sobre no tiene sello, recházalo y avísame" |

## SPF (Sender Policy Framework)

Es un registro **TXT en el DNS** del dominio que lista quién puede enviar correo en su nombre.

```
Nombre:  tudominio.com
Tipo:    TXT
Valor:   v=spf1 include:_spf.google.com include:sendgrid.net ~all
```
- `include:` autoriza a un proveedor (Google Workspace, SendGrid, etc.). Los valores exactos los da cada proveedor.
- `~all` = "los demás, sospechosos (softfail)"; `-all` = "los demás, rechazar". Se empieza con `~all` y se pasa a `-all` cuando todo funciona.
- **Solo puede haber un registro SPF por dominio.** Dos registros SPF se invalidan entre sí.
- **Límite de 10 consultas DNS** (cada `include` suma). Pasarse hace fallar SPF.
- SPF valida el **dominio del "Return-Path"/envelope**, no el `From:` que ve la persona (de ahí que DMARC exija *alineación*).

## DKIM (DomainKeys Identified Mail)

El servidor que envía **firma** el mensaje con una clave privada; el receptor verifica la firma con la **clave pública publicada en el DNS**.

```
Nombre:  selector1._domainkey.tudominio.com
Tipo:    TXT   (o CNAME, según el proveedor)
Valor:   v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GN...   ← clave pública
```
- El **selector** (`selector1`) permite tener varias claves y rotarlas sin cortar el envío.
- El proveedor de correo genera el par de claves y te da el registro exacto; tú solo lo pegas en el DNS y activas la firma.
- Protege la integridad: si alguien altera el cuerpo en tránsito, la firma deja de coincidir.

## DMARC (Domain-based Message Authentication, Reporting & Conformance)

Une SPF y DKIM y le dice al receptor **qué hacer** si fallan. Además exige **alineación**: el dominio del `From:` visible debe coincidir con el dominio validado por SPF **o** DKIM.

```
Nombre:  _dmarc.tudominio.com
Tipo:    TXT
Valor:   v=DMARC1; p=none; rua=mailto:dmarc-reportes@tudominio.com; pct=100; adkim=r; aspf=r
```
- `p=` es la política: **`none`** (solo monitorear), **`quarantine`** (mandar a spam), **`reject`** (rechazar).
- `rua=` es dónde llegan los **reportes agregados** (XML) para ver quién envía en tu nombre.
- `pct=` aplica la política solo a un porcentaje de correos, útil para ir subiendo con cuidado.

## Cómo lo configuraría para un dominio de envío (paso a paso)

1. **Usar un subdominio de envío** para outreach (p. ej. `mail.tudominio.com` o `hola.tudominio.com`) y no el dominio principal: si el outreach daña la reputación, el correo normal de la empresa no se afecta.
2. **Elegir el proveedor de envío** (Google Workspace, SendGrid, Amazon SES, Postmark…) y añadir el dominio en su panel.
3. **SPF:** publicar el registro TXT con el `include:` del proveedor (un solo registro SPF).
4. **DKIM:** publicar los registros que da el proveedor (TXT/CNAME con el selector) y activar la firma.
5. **DMARC:** publicar `p=none` con `rua=` a un buzón que alguien lea.
6. **Verificar** con las herramientas del proveedor y enviando un correo de prueba a Gmail → "Mostrar original" y revisar `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`.
7. **Observar 2–4 semanas** los reportes DMARC para confirmar que todo el correo legítimo pasa y que nadie más envía en tu nombre.
8. **Endurecer la política poco a poco:** `p=none` → `p=quarantine` (con `pct=10`, luego 50, luego 100) → `p=reject`.
9. **Calentar el dominio (warm-up)** antes de enviar volumen.

## Warm-up (calentamiento) en una frase

Un dominio nuevo no tiene reputación: si de golpe envías cientos de correos, los filtros lo ven como sospechoso. Se empieza con **pocos correos al día a gente que probablemente responda**, y se aumenta el volumen **gradualmente** durante semanas, vigilando rebotes y quejas. La reputación depende de que la gente abra, responda y no marque spam.

## Otras buenas prácticas que mejoran la entregabilidad

- **Baja fácil:** línea "responde BAJA" y cabecera `List-Unsubscribe` (el demo ya la incluye en `SMTPSender`).
- **Limpiar la lista:** no enviar a direcciones inválidas; los rebotes duros dañan la reputación.
- **Texto relevante y personalizado**, sin exceso de enlaces, imágenes pesadas ni palabras de spam.
- **Mantener tasa de quejas baja.** Los grandes buzones han endurecido las reglas para quien envía volumen: exigen autenticación (SPF, DKIM y DMARC) y baja sencilla, y vigilan las quejas de spam. Conviene revisar los requisitos vigentes de Google y Yahoo antes de enviar volumen, porque cambian.

## Cómo explicarlo en la entrevista (30 segundos)

> "SPF dice qué servidores pueden enviar por el dominio, DKIM firma el mensaje para probar que no se alteró, y DMARC dice qué hacer si fallan y me manda reportes. Yo usaría un subdominio de envío, publicaría SPF y DKIM del proveedor, empezaría DMARC en `p=none` para observar y lo subiría a `quarantine` y luego `reject`, y calentaría el dominio de a poco antes de enviar volumen. En mi demo no lo usé porque los correos se escriben a archivo; lo tengo claro en teoría pero no lo he configurado en producción."
