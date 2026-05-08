import { useQuery } from '@tanstack/react-query'
import { getAppConfig } from '@/lib/api'

export function TermsOfServicePage() {
  const { data: appConfig } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
    staleTime: 5 * 60 * 1000,
  })

  const addonName = appConfig?.addon_name || 'MediaFusion'
  const contactEmail = appConfig?.contact_email

  return (
    <div className="max-w-3xl mx-auto py-8">
      <h1 className="text-3xl font-bold mb-8">Terms of Service</h1>
      <p className="text-sm text-muted-foreground mb-8">Last updated: February 2026</p>

      <div className="prose dark:prose-invert max-w-none space-y-6">
        <section>
          <h2 className="text-xl font-semibold mb-3">1. Acceptance of Terms</h2>
          <p className="text-muted-foreground leading-relaxed">
            By accessing or using {addonName} ("the Service"), you agree to be bound by these Terms of Service
            ("Terms"). If you do not agree to these Terms, you may not use the Service.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">2. Description of Service</h2>
          <p className="text-muted-foreground leading-relaxed">
            {addonName} is <strong>open-source software</strong> that allows users to organize and manage streams from
            their own configured sources. The software acts as a middleware layer that connects user-provided indexers,
            APIs, and streaming providers into a unified interface compatible with media applications such as Stremio
            and Kodi. {addonName} does not host, store, or distribute any media content itself.
          </p>
          <p className="text-muted-foreground leading-relaxed mt-2">
            Each instance of {addonName} is <strong>independently operated</strong> by its own administrator (hoster).
            The developers of the {addonName} open-source project do not operate, control, or take responsibility for
            any specific hosted instance or the content accessible through it. This instance is operated by its own
            administrator, who is solely responsible for its operation and compliance with applicable laws.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">3. User Responsibilities</h2>
          <p className="text-muted-foreground leading-relaxed mb-2">As a user of the Service, you agree to:</p>
          <ul className="list-disc pl-6 text-muted-foreground space-y-1">
            <li>
              Comply with all applicable local, national, and international laws and regulations when using the Service.
            </li>
            <li>
              Take full responsibility for the content sources, indexers, and streaming providers you configure within
              the Service.
            </li>
            <li>Not use the Service to infringe upon the intellectual property rights of others.</li>
            <li>
              Not attempt to disrupt, overload, or interfere with the proper functioning of the Service or its
              infrastructure.
            </li>
            <li>Keep your account credentials secure and not share them with unauthorized parties.</li>
            <li>Not use automated tools to excessively access or scrape the Service beyond normal usage patterns.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">4. Intellectual Property</h2>
          <p className="text-muted-foreground leading-relaxed">
            {addonName} is open-source software. The source code is available under its respective license on GitHub.
            While the software is freely available, the Service infrastructure (servers, domain, databases) is operated
            and maintained by the instance administrator. Users retain ownership of any content they contribute or
            configure through the Service.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">5. Content and Copyright</h2>
          <p className="text-muted-foreground leading-relaxed">
            The {addonName} open-source project respects the intellectual property rights of others. Each instance
            administrator is responsible for ensuring their instance complies with the Digital Millennium Copyright Act
            (DMCA) and similar legislation. The developers of {addonName}
            are not responsible for content accessible on any specific instance.
          </p>
          <p className="text-muted-foreground leading-relaxed mt-2">
            If you believe that content accessible through this instance infringes your copyright, please see our{' '}
            <a href="/app/dmca" className="text-primary underline hover:text-primary/80">
              DMCA Policy
            </a>{' '}
            for instructions on how to submit a takedown request to the instance administrator.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">6. Account Termination</h2>
          <p className="text-muted-foreground leading-relaxed">
            We reserve the right to suspend or terminate your account at any time if you violate these Terms, engage in
            abusive behavior, or use the Service in a manner that could expose us or other users to legal liability. You
            may also delete your account at any time through your account settings.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">7. Disclaimer of Warranties</h2>
          <p className="text-muted-foreground leading-relaxed">
            THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR
            IMPLIED, INCLUDING BUT NOT LIMITED TO IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
            PURPOSE, AND NON-INFRINGEMENT. WE DO NOT WARRANT THAT THE SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR
            SECURE. USE OF THE SERVICE IS AT YOUR OWN RISK.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">8. Limitation of Liability</h2>
          <p className="text-muted-foreground leading-relaxed">
            TO THE MAXIMUM EXTENT PERMITTED BY LAW, IN NO EVENT SHALL {addonName.toUpperCase()}, ITS CONTRIBUTORS, OR
            ITS OPERATORS BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR ANY
            LOSS OF PROFITS OR REVENUES, WHETHER INCURRED DIRECTLY OR INDIRECTLY, OR ANY LOSS OF DATA, USE, GOODWILL, OR
            OTHER INTANGIBLE LOSSES, RESULTING FROM YOUR ACCESS TO OR USE OF THE SERVICE.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">9. Indemnification</h2>
          <p className="text-muted-foreground leading-relaxed">
            You agree to indemnify and hold harmless {addonName}, its operators, and contributors from any claims,
            damages, losses, or expenses (including reasonable attorney's fees) arising from your use of the Service,
            your violation of these Terms, or your violation of any third-party rights.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">10. Modifications to Terms</h2>
          <p className="text-muted-foreground leading-relaxed">
            We reserve the right to modify these Terms at any time. Changes will be effective immediately upon posting
            to this page. Your continued use of the Service following any changes constitutes acceptance of the revised
            Terms. We encourage you to review these Terms periodically.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">11. Governing Law</h2>
          <p className="text-muted-foreground leading-relaxed">
            These Terms shall be governed by and construed in accordance with applicable laws, without regard to
            conflict of law principles. Any disputes arising from these Terms or the Service shall be resolved through
            good-faith negotiation, and if necessary, through binding arbitration.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-semibold mb-3">12. Contact</h2>
          <p className="text-muted-foreground leading-relaxed">
            If you have questions about these Terms as they apply to this instance, please contact the instance
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
