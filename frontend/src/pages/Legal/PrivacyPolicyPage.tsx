import { useQuery } from '@tanstack/react-query'
import { getAppConfig } from '@/lib/api'

export function PrivacyPolicyPage() {
  const { data: appConfig } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
    staleTime: 5 * 60 * 1000,
  })

  const addonName = appConfig?.addon_name || 'MediaFusion'
  const contactEmail = appConfig?.contact_email

  return (
    <div className="max-w-3xl mx-auto py-8">
      <h1 className="text-3xl font-bold mb-8">Privacy Policy</h1>
      <p className="text-sm text-muted-foreground mb-8">Last updated: February 2026</p>

      <div className="prose dark:prose-invert max-w-none space-y-6">
        <section>
          <h2 className="text-xl font-semibold mb-3">1. Introduction</h2>
          <p className="text-muted-foreground leading-relaxed">
            {addonName} is <strong>open-source software</strong> that can be self-hosted by anyone. This Privacy Policy
            applies to <strong>this specific instance</strong> of {addonName}, which is independently operated by its
            own administrator (hoster). The developers of the
            {addonName} open-source project do not operate or control any specific hosted instance.
          </p>
          <p className="text-muted-foreground leading-relaxed mt-2">
            This policy describes how this instance collects, uses, and protects your information when you use the
            service. The instance administrator is committed to protecting your privacy and handling your data
            transparently.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">2. Information We Collect</h2>
          <p className="text-muted-foreground leading-relaxed mb-2">
            We collect the minimum information necessary to provide and improve the Service:
          </p>
          <ul className="list-disc pl-6 text-muted-foreground space-y-1">
            <li>
              <strong>Account Information:</strong> When you create an account, we collect your username, email address,
              and a securely hashed password.
            </li>
            <li>
              <strong>Configuration Data:</strong> Your addon configuration preferences, profile settings, and streaming
              provider tokens (stored in encrypted form).
            </li>
            <li>
              <strong>Watch History:</strong> If enabled, your viewing history for features like "Continue Watching" and
              watchlist synchronization.
            </li>
            <li>
              <strong>Usage Data:</strong> Basic request logs and error reports to maintain service reliability. These
              do not contain personally identifiable content.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">3. How We Use Your Information</h2>
          <ul className="list-disc pl-6 text-muted-foreground space-y-1">
            <li>To provide, operate, and maintain the Service</li>
            <li>To personalize your experience through saved profiles and configurations</li>
            <li>To synchronize your watchlist and watch history across devices</li>
            <li>To improve and optimize the Service based on aggregate usage patterns</li>
            <li>To communicate important service updates or security notices</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">4. Data Storage and Security</h2>
          <ul className="list-disc pl-6 text-muted-foreground space-y-1">
            <li>
              All sensitive configuration data (streaming provider tokens, API keys) is encrypted at rest using
              industry-standard encryption.
            </li>
            <li>Passwords are hashed using secure, one-way hashing algorithms and are never stored in plaintext.</li>
            <li>
              Stremio addon configuration URLs are cryptographically signed to prevent tampering with user settings.
            </li>
            <li>We do not sell, rent, or share your personal information with third parties for marketing purposes.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">5. Third-Party Services</h2>
          <p className="text-muted-foreground leading-relaxed">
            The Service may integrate with third-party streaming providers and metadata services (such as IMDB, TMDB,
            Trakt, Simkl) based on your configuration. When you connect these services, your interactions with them are
            governed by their respective privacy policies. We only transmit the minimum data necessary for the
            integration to function.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">6. Cookies and Local Storage</h2>
          <p className="text-muted-foreground leading-relaxed">
            We use browser local storage to save your authentication tokens, theme preferences, and API key for private
            instances. We do not use third-party tracking cookies or analytics services that track individual users.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">7. Your Rights</h2>
          <p className="text-muted-foreground leading-relaxed mb-2">You have the right to:</p>
          <ul className="list-disc pl-6 text-muted-foreground space-y-1">
            <li>Access your personal data stored in your account</li>
            <li>Update or correct your account information at any time</li>
            <li>Delete your account and all associated data</li>
            <li>Export your configuration and profile data</li>
            <li>Withdraw consent for optional data collection (e.g., watch history)</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">8. Data Retention</h2>
          <p className="text-muted-foreground leading-relaxed">
            We retain your data only for as long as your account is active or as needed to provide the Service. When you
            delete your account, all associated personal data is permanently removed from our systems. Anonymized,
            aggregate data may be retained for service improvement purposes.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">9. Children's Privacy</h2>
          <p className="text-muted-foreground leading-relaxed">
            The Service is not intended for use by children under the age of 13. We do not knowingly collect personal
            information from children under 13. If we become aware that we have collected such information, we will take
            steps to delete it promptly.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">10. Changes to This Policy</h2>
          <p className="text-muted-foreground leading-relaxed">
            We may update this Privacy Policy from time to time. We will notify users of any material changes by posting
            the new policy on this page with an updated revision date. Your continued use of the Service after any
            changes constitutes acceptance of the updated policy.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">11. Contact Us</h2>
          <p className="text-muted-foreground leading-relaxed">
            If you have questions about this Privacy Policy as it applies to this instance, please contact the instance
            administrator
            {contactEmail ? (
              <>
                {' '}
                at{' '}
                <a href={`mailto:${contactEmail}`} className="text-primary underline hover:text-primary/80">
                  {contactEmail}
                </a>
              </>
            ) : null}
            . For questions about the {addonName} open-source software itself, please open an issue on the{' '}
            <a
              href="https://github.com/mhdzumair/MediaFusion"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline hover:text-primary/80"
            >
              GitHub repository
            </a>
            .
          </p>
        </section>
      </div>
    </div>
  )
}
