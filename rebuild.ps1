git status
git add -A
git commit -m "Integrating SQLite Share Ledger for Stratum Gateway"
git tag v0.1.3
git push origin main
git push origin v0.1.3

docker build -t ghcr.io/satoshiware/azcoin-node-api:v0.1.3 .

docker push ghcr.io/satoshiware/azcoin-node-api:v0.1.3