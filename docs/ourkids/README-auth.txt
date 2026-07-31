middleware.js + package.json are duplicated here on purpose.

If anything ever deploys THIS folder as the Vercel root (an older workflow used to do
exactly that), the copy at docs/ lands outside the deployment and the site goes live with
no password at all. Keeping a copy here makes that failure mode fail closed instead.
Do not delete without checking every workflow's deploy path.
