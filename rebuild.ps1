git status
git add -A
git commit -m "Major update. Added bare metal linux install removed pool monitoring and added new translator monitoring 0.1.7"
git tag v0.1.7
git push origin main
git push origin v0.1.7

$SHA = (git rev-parse --short HEAD).Trim()

# Build once, tag many (v0.1.7 + stable + sha; optionally latest)
$SHA = (git rev-parse --short HEAD).Trim()

docker build `
  -t ghcr.io/satoshiware/azcoin-node-api:sha-$SHA `
  -t ghcr.io/satoshiware/azcoin-node-api:latest `
  .

docker push ghcr.io/satoshiware/azcoin-node-api:sha-$SHA
docker push ghcr.io/satoshiware/azcoin-node-api:latest
