FROM alpine:3.21

RUN apk add --no-cache openssl
COPY docker/init-tls.sh /usr/local/bin/init-tls
ENTRYPOINT ["sh", "/usr/local/bin/init-tls"]
