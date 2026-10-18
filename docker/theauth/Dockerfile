FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/theauth .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/theauth /theauth
VOLUME /data
ENV THEAUTH_DB_PATH=/data/theauth.db
EXPOSE 9200
ENTRYPOINT ["/theauth"]
