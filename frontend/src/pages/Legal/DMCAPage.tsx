import { useQuery } from '@tanstack/react-query'
import { getAppConfig } from '@/lib/api'

export function DMCAPage() {
  const { data: appConfig } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
    staleTime: 5 * 60 * 1000,
  })

  const addonName = appConfig?.addon_name || 'MediaFusion'
  const contactEmail = appConfig?.contact_email

  return (
    <div className="max-w-3xl mx-auto py-8">
      <h1 className="text-3xl font-bold mb-8">Content Infringement Report (DMCA Policy)</h1>
      <p className="text-sm text-muted-foreground mb-8">Last updated: February 2026</p>

      <div className="prose dark:prose-invert max-w-none space-y-6">
        <section>
          <h2 className="text-xl font-semibold mb-3">Overview</h2>
          <p className="text-muted-foreground leading-relaxed">
            {addonName} is <strong>open-source software</strong> that can be self-hosted by anyone. The developers of{' '}
            {addonName} do not operate, control, or take responsibility for any specific hosted instance of this
            software. Each instance is independently operated by its own administrator (hoster).
          </p>
          <p className="text-muted-foreground leading-relaxed mt-2">
            {addonName} does not host, store, or distribute any media content. It acts as middleware that connects
            user-configured sources. If you believe that content accessible through <strong>this instance</strong>{' '}
            infringes your copyright, you should direct your takedown request to the instance administrator as described
            below.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">Filing a DMCA Takedown Notice</h2>
          <p className="text-muted-foreground leading-relaxed mb-3">
            To file a valid DMCA takedown notice, please provide the following information:
          </p>
          <ol className="list-decimal pl-6 text-muted-foreground space-y-2">
            <li>
              <strong>Identification of the copyrighted work:</strong> A description of the copyrighted work you claim
              has been infringed, or if multiple works are covered by a single notification, a representative list.
            </li>
            <li>
              <strong>Identification of the infringing material:</strong> The specific URL(s), info hash(es), or other
              identifying information for the material you claim is infringing, with enough detail for us to locate it.
            </li>
            <li>
              <strong>Your contact information:</strong> Your name, mailing address, telephone number, and email
              address.
            </li>
            <li>
              <strong>Good faith statement:</strong> A statement that you have a good faith belief that the use of the
              material is not authorized by the copyright owner, its agent, or the law.
            </li>
            <li>
              <strong>Accuracy statement:</strong> A statement, made under penalty of perjury, that the information in
              the notification is accurate and that you are the copyright owner or are authorized to act on behalf of
              the copyright owner.
            </li>
            <li>
              <strong>Signature:</strong> Your physical or electronic signature.
            </li>
          </ol>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">How to Submit</h2>
          <p className="text-muted-foreground leading-relaxed">
            Please submit your DMCA takedown notice directly to the <strong>administrator of this instance</strong>.
            Each hosted instance of {addonName} is independently operated, and the instance administrator is the
            responsible party for content accessible through their service.
          </p>
          {contactEmail && (
            <p className="text-muted-foreground leading-relaxed mt-2">
              You can reach the administrator of this instance at{' '}
              <a
                href={`mailto:${contactEmail}?subject=DMCA Takedown Request`}
                className="text-primary underline hover:text-primary/80"
              >
                {contactEmail}
              </a>
              .
            </p>
          )}
          <p className="text-muted-foreground leading-relaxed mt-2">
            If you have questions about the {addonName} open-source software itself (not content on a specific
            instance), you may open an issue on the{' '}
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

        <section>
          <h2 className="text-xl font-semibold mb-3">What Happens After You Submit</h2>
          <p className="text-muted-foreground leading-relaxed mb-2">
            The instance administrator is responsible for processing takedown requests. Generally, a properly operated
            instance will:
          </p>
          <ul className="list-disc pl-6 text-muted-foreground space-y-1">
            <li>
              Review the notice and, if valid and complete, promptly remove or disable access to the allegedly
              infringing material.
            </li>
            <li>
              Make a good faith effort to notify the user who provided the content (if applicable) that the material has
              been removed.
            </li>
            <li>
              For torrent-based content, block the identified info hashes to prevent further access through the
              instance.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">Counter-Notification</h2>
          <p className="text-muted-foreground leading-relaxed mb-3">
            If you believe that material was removed or disabled as a result of a mistake or misidentification, you may
            submit a counter-notification containing:
          </p>
          <ol className="list-decimal pl-6 text-muted-foreground space-y-2">
            <li>Your name, address, and telephone number.</li>
            <li>Identification of the material that was removed and the location where it appeared before removal.</li>
            <li>
              A statement under penalty of perjury that you have a good faith belief that the material was removed or
              disabled as a result of mistake or misidentification.
            </li>
            <li>
              A statement that you consent to the jurisdiction of the federal court in your district (or, if outside the
              United States, any judicial district in which the service provider may be found), and that you will accept
              service of process from the person who provided the original DMCA notification.
            </li>
            <li>Your physical or electronic signature.</li>
          </ol>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">Repeat Infringers</h2>
          <p className="text-muted-foreground leading-relaxed">
            Instance administrators are expected to maintain a policy of terminating accounts of users who are repeat
            infringers, in accordance with the DMCA and other applicable laws.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">Disclaimer</h2>
          <p className="text-muted-foreground leading-relaxed">
            This policy is provided for informational purposes and does not constitute legal advice. If you are unsure
            whether material infringes your copyright, you should consult with an attorney before submitting a DMCA
            notice.
          </p>
        </section>
      </div>
    </div>
  )
}
